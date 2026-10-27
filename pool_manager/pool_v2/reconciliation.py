"""Recover owned precommits, sampled nonces and confirmed TIG outcomes.

Only stored complete block observations drive this adapter. A disappeared
record is not an expiry signal, and a proof POST is not an activation signal.
"""

from . import benchmarks, member_protocol, submissions, qualifiers, reports
from .block_observer import BlockStore
from .money import Conflict
from .protocol import ProtocolDataError, _index, _integer
from datetime import datetime
import hashlib
from psycopg2.extras import Json


def player_feed(observation, player_id):
    if player_id in observation["players"]:
        feed = observation["players"][player_id]
    elif observation.get("pool_player_id") == player_id:
        feed = observation.get("pool_pending")
    else:
        feed = None
    if not isinstance(feed, dict):
        raise ProtocolDataError("this block did not capture the pool's pending/active benchmark feed")
    try:
        indexed = {name: _index(feed[name], key, name) for name,key in
                   (("precommits","benchmark_id"),("benchmarks","id"),("proofs","benchmark_id"),("frauds","benchmark_id"))}
        for value in indexed["precommits"].values():
            if value["settings"]["player_id"] != player_id:
                raise ProtocolDataError("pool precommit feed contains another owner")
        return indexed
    except (KeyError,TypeError,AttributeError) as exc:
        raise ProtocolDataError("pool benchmark feed is incomplete") from exc


def confirmed(record, height):
    if not record or record.get("state") is None:
        return False
    value = _integer(record["state"]["block_confirmed"], "protocol confirmation", 1)
    if value > height:
        raise ProtocolDataError("protocol record was confirmed after the observed block")
    return True


def _expired_lifetime(database, row, observation, snapshot):
    """Retain both validated block anchors for a strictly elapsed lifetime."""
    if row['benchmark_id'] in snapshot.precommits:
        return None
    try:
        original, original_snapshot = BlockStore(database).read(row['selection']['block_id'])
        settings = row['assignment']['settings']
        challenge = settings['challenge_id']
        lifetime = _integer(original['start']['block']['config']['challenges'][challenge]['lifespan_period'],
                            'challenge lifetime', 1)
        current_lifetime = _integer(observation['start']['block']['config']['challenges'][challenge]['lifespan_period'],
                                    'current challenge lifetime', 1)
        started = _integer(row['selection']['height'], 'precommit start height')
        if settings['block_id'] != original_snapshot.block_id or started != original_snapshot.height:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if current_lifetime != lifetime or snapshot.height <= started + lifetime:
        return None
    return {'original_block_id': original_snapshot.block_id, 'block_started': started,
            'lifespan_period': lifetime, 'block_id': snapshot.block_id, 'height': snapshot.height}


def _expire_unsent_results(database, row, observation, snapshot):
    """End handed-over work whose results could never have reached TIG.

    The caller supplies a complete feed containing no record for this benchmark.
    The elapsed protocol lifetime and durable send fences establish expiry even
    when the member never uploads anything. A possibly sent payload stays held.
    """
    if row['handed_over_at'] is None:
        return False
    # Avoid replaying the creation block for every unresolved external write.
    # This is only a read optimization; all send fences are checked again below.
    with database.transaction() as cursor:
        cursor.execute("""SELECT 1 FROM protocol_outbox WHERE reservation_id=%s
            AND kind IN ('results','proofs')
            AND (kind='proofs' OR state<>'ready' OR sent_at IS NOT NULL) LIMIT 1""", (row['id'],))
        if cursor.fetchone():
            return False
    lifetime = _expired_lifetime(database, row, observation, snapshot)
    if lifetime is None:
        return False
    with database.transaction() as cursor:
        # Serialize against uploads, first sends and other outcome transitions.
        locked = benchmarks._locked(cursor, row['id'], budget=True)
        if locked['state'] != 'accepted' or locked['handed_over_at'] is None:
            return False
        cursor.execute("""SELECT * FROM protocol_outbox WHERE reservation_id=%s
            AND kind IN ('results','proofs') ORDER BY kind FOR UPDATE""", (row['id'],))
        intents = cursor.fetchall()
        if any(intent['kind'] == 'proofs' or intent['state'] != 'ready' or intent['sent_at'] is not None
               for intent in intents):
            return False
        cursor.execute("""SELECT 1 FROM submission_responses s JOIN protocol_outbox o ON o.id=s.outbox_id
            WHERE o.reservation_id=%s AND o.kind IN ('results','proofs') LIMIT 1""", (row['id'],))
        if cursor.fetchone():
            return False
        cursor.execute("SELECT 1 FROM benchmark_progress WHERE benchmark_id=%s", (row['benchmark_id'],))
        if cursor.fetchone():
            return False
        evidence = {**lifetime, 'source': 'tig-unsent-results-expiry-v1', 'results_sent': False,
                    'result_intent_id': str(intents[0]['id']) if intents else None}
        benchmarks.record_outcome(database, row['id'], 'expired', height=snapshot.height,
                                  evidence=evidence, _cursor=cursor)
        # Preserve immutable member payloads, but prevent a queued late first POST.
        for intent in intents:
            cursor.execute("UPDATE protocol_outbox SET state='cancelled',evidence=%s WHERE id=%s",
                           (Json(evidence), intent['id']))
    return True


def _expire_rejected_results(database, row, observation, snapshot, submission_origin):
    """A pinned, explicit late-result rejection can end work without releasing funds.

    Absence, elapsed time and generic HTTP failures remain insufficient. Require
    the original challenge lifetime, a complete later feed, and the exact TIG
    response to this benchmark's first result submission. No POST is retried.
    """
    if not submission_origin:
        return False
    with database.transaction() as cursor:
        cursor.execute("""SELECT s.id,s.response FROM submission_responses s
            JOIN protocol_outbox o ON o.id=s.outbox_id
            WHERE o.reservation_id=%s AND o.kind='results' AND o.state='uncertain'
            ORDER BY s.created_at DESC LIMIT 1""", (row['id'],))
        receipt = cursor.fetchone()
    if not receipt:
        return False
    response = receipt['response']
    body = 'Precommit does not exist: ' + row['benchmark_id']
    if (response.get('status'), response.get('path'), response.get('origin'), response.get('body')) == (
            400, '/submit-benchmark', submission_origin, body) and \
            response.get('raw_sha256') == hashlib.sha256(body.encode()).hexdigest():
        source = 'tig-late-results-rejection-v1'
    elif submissions.rate_limited(response, 'results', submission_origin):
        # TIG's rate limiter refused the results before recording them; the pool never
        # resends a potentially sent payload, so the work ends when its lifetime does.
        source = 'tig-rate-limited-results-v1'
    else:
        return False
    try:
        received = datetime.fromisoformat(response['received_at'])
        if received.utcoffset() is None or snapshot.timestamp < received.timestamp():
            return False
    except (KeyError, TypeError, ValueError):
        return False
    lifetime = _expired_lifetime(database, row, observation, snapshot)
    if lifetime is None:
        return False
    evidence = {**lifetime, 'source': source, 'response_id': str(receipt['id']),
                'response_sha256': response['raw_sha256'], 'origin': submission_origin}
    with database.transaction() as cursor:
        locked = benchmarks._locked(cursor, row['id'], budget=True)
        if locked['state'] != 'accepted':
            return False
        cursor.execute("SELECT * FROM protocol_outbox WHERE reservation_id=%s AND kind='results' FOR UPDATE",
                       (row['id'],))
        intent = cursor.fetchone()
        if not intent or intent['state'] != 'uncertain':
            return False
        cursor.execute("SELECT 1 FROM protocol_outbox WHERE reservation_id=%s AND kind='proofs' LIMIT 1", (row['id'],))
        if cursor.fetchone():
            return False
        cursor.execute("""SELECT 1 FROM submission_responses WHERE outbox_id=%s
            AND response->>'status'='200' LIMIT 1""", (intent['id'],))
        if cursor.fetchone():
            return False
        benchmarks.record_outcome(database, row['id'], 'expired', height=snapshot.height,
                                  evidence=evidence, _cursor=cursor)
        cursor.execute("UPDATE protocol_outbox SET state='rejected',evidence=%s WHERE id=%s",
                       (Json(evidence), intent['id']))
    return True


def _expire_after_lifetime(database, row, observation, snapshot, player_id):
    """End handed-over work whose TIG lifetime ended without activation.

    TIG keeps a benchmark in the pool player's feed until its challenge lifetime
    ends, so the archived block at exactly block_started + lifespan_period records
    the benchmark's final state: a proof confirmed too late to activate (TIG delays
    activation by the submission delay, so block_active lies past the lifetime), or
    confirmed results with no proof at all (for example a proof send that timed
    out). Both are read from that block, never inferred from absence or elapsed
    time alone; a missing boundary block keeps the work held.
    """
    if row['handed_over_at'] is None:
        return False
    lifetime = _expired_lifetime(database, row, observation, snapshot)
    if lifetime is None:
        return False
    boundary = lifetime['block_started'] + lifetime['lifespan_period']
    with database.transaction() as cursor:
        cursor.execute("SELECT id FROM observed_blocks WHERE height=%s", (boundary,))
        last = cursor.fetchone()
    if not last:
        return False
    identity = row['benchmark_id']
    try:
        final_observation, final = BlockStore(database).read(last['id'])
        feed = player_feed(final_observation, player_id)
        precommit, benchmark, proof, fraud = (feed[name].get(identity) for name in
                                              ('precommits', 'benchmarks', 'proofs', 'frauds'))
        if not confirmed(precommit, final.height) or not confirmed(benchmark, final.height) or fraud is not None:
            return False
        if proof is None:
            source, detail = 'tig-unproven-results-expiry-v1', {}
        else:
            if not confirmed(proof, final.height):
                return False
            block_active = _integer(proof['details']['block_active'], 'proof.block_active', 1)
            if block_active <= final.height:
                return False  # It activated inside the lifetime; the active-set path owns that outcome.
            source = 'tig-expired-before-activation-v1'
            detail = {'proof_block_confirmed': proof['state']['block_confirmed'], 'block_active': block_active,
                      'submission_delay': proof['details'].get('submission_delay')}
    except (ProtocolDataError, KeyError, TypeError, ValueError):
        return False
    evidence = {**lifetime, 'source': source, 'boundary_block_id': final.block_id, 'boundary_height': final.height,
                **detail}
    with database.transaction() as cursor:
        # Serialize against uploads, first sends and other outcome transitions.
        locked = benchmarks._locked(cursor, row['id'], budget=True)
        if locked['state'] != 'accepted' or locked['handed_over_at'] is None:
            return False
        cursor.execute("""SELECT * FROM protocol_outbox WHERE reservation_id=%s
            AND kind IN ('results','proofs') ORDER BY kind FOR UPDATE""", (row['id'],))
        intents = {intent['kind']: intent for intent in cursor.fetchall()}
        results, proofs = intents.get('results'), intents.get('proofs')
        if not results or results['state'] != 'accepted':
            return False
        evidence['results_intent_id'] = str(results['id'])
        evidence['proofs_intent'] = {'id': str(proofs['id']), 'state': proofs['state']} if proofs else None
        benchmarks.record_outcome(database, row['id'], 'expired', height=snapshot.height,
                                  evidence=evidence, _cursor=cursor)
        if proofs and proofs['state'] in ('ready', 'uncertain'):
            # A potentially sent proof is never resent, and an unsent one must not go out after expiry.
            cursor.execute("UPDATE protocol_outbox SET state=%s,evidence=%s WHERE id=%s",
                           ('rejected' if proofs['state'] == 'uncertain' else 'cancelled', Json(evidence), proofs['id']))
    return True


def _expire_unconfirmed_results(database, row, observation, snapshot, player_id):
    """End handed-over work whose sent results TIG never recorded inside the lifetime.

    A results POST answered by a transport failure (a gateway error, a timeout)
    leaves the intent uncertain, and the pool never resends it. TIG keeps the
    confirmed precommit in the pool player's feed until the challenge lifetime
    ends and lists the benchmark there from the block its results are confirmed,
    so the archived block at exactly block_started + lifespan_period settles the
    question: a confirmed precommit with no benchmark, proof or fraud record
    there means the results never arrived in time, and TIG accepts none after
    it. The outcome is read from that block, never inferred from the failed
    response or elapsed time alone; a missing boundary block, an unconfirmed
    precommit or a listed benchmark keeps the work held.
    """
    if row['handed_over_at'] is None:
        return False
    with database.transaction() as cursor:
        cursor.execute("""SELECT 1 FROM protocol_outbox WHERE reservation_id=%s AND kind='results'
            AND state='uncertain' LIMIT 1""", (row['id'],))
        if not cursor.fetchone():
            return False
    lifetime = _expired_lifetime(database, row, observation, snapshot)
    if lifetime is None:
        return False
    boundary = lifetime['block_started'] + lifetime['lifespan_period']
    with database.transaction() as cursor:
        cursor.execute("SELECT id FROM observed_blocks WHERE height=%s", (boundary,))
        last = cursor.fetchone()
    if not last:
        return False
    identity = row['benchmark_id']
    try:
        final_observation, final = BlockStore(database).read(last['id'])
        feed = player_feed(final_observation, player_id)
        precommit, benchmark, proof, fraud = (feed[name].get(identity) for name in
                                              ('precommits', 'benchmarks', 'proofs', 'frauds'))
        if not confirmed(precommit, final.height) or benchmark is not None or proof is not None or fraud is not None:
            return False
    except (ProtocolDataError, KeyError, TypeError, ValueError):
        return False
    evidence = {**lifetime, 'source': 'tig-unconfirmed-results-expiry-v1', 'boundary_block_id': final.block_id,
                'boundary_height': final.height}
    with database.transaction() as cursor:
        # Serialize against uploads, first sends and other outcome transitions.
        locked = benchmarks._locked(cursor, row['id'], budget=True)
        if locked['state'] != 'accepted' or locked['handed_over_at'] is None:
            return False
        cursor.execute("""SELECT * FROM protocol_outbox WHERE reservation_id=%s
            AND kind IN ('results','proofs') ORDER BY kind FOR UPDATE""", (row['id'],))
        intents = {intent['kind']: intent for intent in cursor.fetchall()}
        results, proofs = intents.get('results'), intents.get('proofs')
        if not results or results['state'] != 'uncertain':
            return False
        cursor.execute("""SELECT id,response->>'status' AS status FROM submission_responses WHERE outbox_id=%s
            ORDER BY created_at DESC LIMIT 1""", (results['id'],))
        last_response = cursor.fetchone()
        evidence['results_intent_id'] = str(results['id'])
        evidence['results_sent_at'] = results['sent_at'].isoformat() if results['sent_at'] else None
        evidence['last_response'] = ({'id': str(last_response['id']), 'status': last_response['status']}
                                     if last_response else None)
        evidence['proofs_intent'] = {'id': str(proofs['id']), 'state': proofs['state']} if proofs else None
        benchmarks.record_outcome(database, row['id'], 'expired', height=snapshot.height,
                                  evidence=evidence, _cursor=cursor)
        # The potentially sent results are never resent, and nothing may go out after expiry.
        cursor.execute("UPDATE protocol_outbox SET state='rejected',evidence=%s WHERE id=%s",
                       (Json(evidence), results['id']))
        if proofs and proofs['state'] in ('ready', 'uncertain'):
            cursor.execute("UPDATE protocol_outbox SET state=%s,evidence=%s WHERE id=%s",
                           ('rejected' if proofs['state'] == 'uncertain' else 'cancelled', Json(evidence), proofs['id']))
    return True


def _lifespan(observation, challenge):
    return _integer(observation['start']['block']['config']['challenges'][challenge]['lifespan_period'],
                    'challenge lifetime', 1)


def _release_unsent_precommit(database, intent_id, player_id, feed, observation, snapshot, evidence):
    """A precommit still absent from a complete feed well after its send was never received.

    The current block supplies the feed while it lies inside the rule's window. Once it is
    past the window, the newest archived complete block inside the window is read instead,
    so the outcome does not depend on when the coordinator happens to run.
    """
    with database.transaction() as cursor:
        cursor.execute("""SELECT r.payload,o.preflight FROM protocol_outbox o JOIN reservations r ON r.id=o.reservation_id
            WHERE o.id=%s""", (intent_id,))
        row = cursor.fetchone()
    try:
        challenge = row['payload']['settings']['challenge_id']
        started = _integer(row['preflight']['height'], 'preflight height', 1)
        lifespan = _lifespan(observation, challenge)
    except (KeyError, TypeError, ValueError):
        return None
    first, last = started + submissions.UNSENT_PRECOMMIT_MARGIN, started + lifespan // 2
    if snapshot.height >= last:
        with database.transaction() as cursor:
            cursor.execute("SELECT id FROM observed_blocks WHERE height>=%s AND height<%s ORDER BY height DESC LIMIT 1",
                           (first, last))
            archived = cursor.fetchone()
        if not archived:
            return None
        try:
            current = snapshot.height
            observation, snapshot = BlockStore(database).read(archived['id'])
            feed = player_feed(observation, player_id)
            lifespan = _lifespan(observation, challenge)
        except (ProtocolDataError, KeyError, TypeError, ValueError):
            return None
        evidence = {**evidence, 'block_id': snapshot.block_id, 'height': snapshot.height,
                    'source': 'archived-pool-feed', 'evaluated_at_height': current}
    return submissions.expire_unsent_precommit(database, intent_id, list(feed['precommits'].values()),
                                               height=snapshot.height, lifespan=lifespan, evidence=evidence)


def reconcile_block(database, block_id, player_id, *, artifact_origin=None, submission_origin=None):
    observation,snapshot = BlockStore(database).read(block_id)
    feed = player_feed(observation,player_id)
    evidence = {"block_id":snapshot.block_id,"height":snapshot.height,"source":"archived-pool-feed"}
    with database.transaction() as cursor:
        cursor.execute("SELECT id FROM protocol_outbox WHERE kind='precommit' AND state='uncertain' ORDER BY created_at,id")
        uncertain = cursor.fetchall()
    for intent in uncertain:
        if submissions.recover_precommit(database,intent["id"],list(feed["precommits"].values()),evidence=evidence) is None:
            _release_unsent_precommit(database,intent["id"],player_id,feed,observation,snapshot,evidence)
    with database.transaction() as cursor:
        cursor.execute("""SELECT r.id,p.benchmark_id FROM reservations r JOIN precommit_receipts p ON p.reservation_id=r.id
            WHERE r.state='uncertain' ORDER BY r.id""")
        unpublished = cursor.fetchall()
    for row in unpublished:
        precommit = feed["precommits"].get(row["benchmark_id"])
        if confirmed(precommit,snapshot.height):
            submissions.publish_assignment(database,row["id"],precommit,evidence=evidence,artifact_origin=artifact_origin)
    with database.transaction() as cursor:
        cursor.execute("SELECT * FROM reservations WHERE state='accepted' ORDER BY id")
        owned = cursor.fetchall()
    for row in owned:
        identity=row["benchmark_id"]
        benchmark,proof,fraud=(feed[name].get(identity) for name in ("benchmarks","proofs","frauds"))
        if confirmed(benchmark,snapshot.height):
            with database.transaction() as cursor:
                cursor.execute("SELECT payload FROM benchmark_payloads WHERE benchmark_id=%s AND kind='results'",(identity,))
                result=cursor.fetchone()
            if not result or not row["handed_over_at"] or benchmark["details"].get("merkle_root") != result["payload"]["merkle_root"]:
                raise ProtocolDataError("TIG benchmark has no matching member-owned root and handover")
            submissions.confirm_payload(database,row["id"],"results",evidence=evidence)
            if benchmark["details"].get("stopped") is False:
                reports.record_confirmation_round(database, identity, benchmark['state']['block_confirmed'], block_id)
                member_protocol.sampled(database,identity,benchmark["details"].get("sampled_nonces"),evidence=evidence)
        if confirmed(fraud,snapshot.height):
            # This is the confirmed pre-activation verification failure feed.
            # Later nonce arbitration is a separate X+2 settlement input.
            submissions.confirm_payload(database,row["id"],"proofs",evidence=evidence)
            benchmarks.record_outcome(database,row["id"],"verification_failed",height=fraud["state"]["block_confirmed"],evidence=evidence)
        elif confirmed(proof,snapshot.height):
            submissions.confirm_payload(database,row["id"],"proofs",evidence=evidence)
            if identity in snapshot.precommits:
                benchmarks.record_outcome(database,row["id"],"active",height=proof["details"]["block_active"],evidence=evidence)
        elif not any(identity in records for records in feed.values()):
            if not _expire_unsent_results(database, row, observation, snapshot) and \
                    not _expire_rejected_results(database, row, observation, snapshot, submission_origin) and \
                    not _expire_after_lifetime(database, row, observation, snapshot, player_id):
                _expire_unconfirmed_results(database, row, observation, snapshot, player_id)
    return evidence


def reconcile_pending(database, player_id, *, limit=100, artifact_origin=None, submission_origin=None):
    """Replay missed blocks in height order, without letting one hold stop others."""
    with database.transaction() as cursor:
        cursor.execute("""SELECT b.id,b.height FROM observed_blocks b LEFT JOIN reconciled_blocks r ON r.block_id=b.id
            LEFT JOIN reconciliation_queue q ON q.block_id=b.id WHERE r.block_id IS NULL
            ORDER BY coalesce(q.last_attempted_at,to_timestamp(0)),b.height LIMIT %s""", (limit,))
        blocks=cursor.fetchall()
    completed,held=[],[]
    for block in blocks:
        try:
            reconcile_block(database,block["id"],player_id,artifact_origin=artifact_origin,
                            submission_origin=submission_origin)
            qualifiers.credit_block(database,block["id"],player_id)
        except (ProtocolDataError,Conflict) as error:
            held.append({"block_id":block["id"],"height":block["height"],"reason":str(error)})
            with database.transaction() as cursor:
                cursor.execute("""INSERT INTO reconciliation_queue(block_id,last_error) VALUES (%s,%s)
                    ON CONFLICT(block_id) DO UPDATE SET last_attempted_at=clock_timestamp(),last_error=excluded.last_error""",
                    (block["id"],str(error)))
            continue
        with database.transaction() as cursor:
            cursor.execute("INSERT INTO reconciled_blocks(block_id,player_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                           (block["id"],player_id))
        completed.append(block["id"])
    return {"completed":completed,"held":held}
