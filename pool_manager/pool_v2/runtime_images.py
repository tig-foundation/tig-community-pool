"""Pin the official TIG runtime image for every active challenge.

Members' workers run a challenge only inside an explicitly pinned official
image, so somebody has to pin one. The pool does: for each active challenge in
the newest complete snapshot it takes the challenge name TIG publishes in the
challenge configuration, asks the registry which content digest the configured
runtime version tag names, and records the reference. Each assignment then
carries the reference for its challenge. Workers pull the image from the
registry themselves; the pool downloads nothing and redistributes nothing.
"""

import json
import logging
import re
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .protocol import ProtocolDataError

REGISTRY = "https://ghcr.io"
REPOSITORY = "tig-foundation/tig-monorepo"
IMAGE = re.compile(r"ghcr\.io/tig-foundation/tig-monorepo/([a-z_]+)/runtime@sha256:([0-9a-f]{64})")
CHALLENGE = re.compile(r"c[0-9]+")
NAME = re.compile(r"[a-z_]+")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
# Any manifest type the registry may serve for a tag; the digest of a multi-
# architecture index pulls the right platform on AMD64 and ARM64 members alike.
ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"))
log = logging.getLogger("innopool-v2-runtime-images")


class RegistryError(RuntimeError):
    """The registry answered, but not with a usable digest."""


def check_version(version):
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError("the TIG runtime version must be a dotted release number such as 0.0.8")
    return version


def check_images(images):
    """A challenge-to-image map of official pinned references, as selection and the API use it."""
    if not isinstance(images, dict) or any(not isinstance(key, str) or not key
                                           or not isinstance(value, str) or not IMAGE.fullmatch(value)
                                           for key, value in images.items()):
        raise ProtocolDataError("runtime images must map challenge ids to official pinned image digests")
    return dict(images)


def reference(name, digest):
    return f"ghcr.io/{REPOSITORY}/{name}/runtime@sha256:{digest}"


def resolve_digest(name, version, *, timeout=20, opener=urlopen):
    """The content digest the registry serves for this challenge's runtime tag; None while unpublished."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("challenge name must be the lowercase image path segment")
    check_version(version)
    repository = f"{REPOSITORY}/{name}/runtime"
    token_request = Request(REGISTRY + "/token?scope=" + quote(f"repository:{repository}:pull", safe=":/"),
                            headers={"Accept": "application/json", "User-Agent": "innopool-v2-runtime-images/2.0"})
    try:
        with opener(token_request, timeout=timeout) as response:
            token = json.loads(response.read(64 * 1024)).get("token")
    except HTTPError as error:
        if error.code in (401, 403, 404):
            return None  # No such public repository: TIG has not published this challenge's runtime.
        raise
    if not isinstance(token, str) or not token:
        raise RegistryError("registry issued no anonymous pull token")
    request = Request(f"{REGISTRY}/v2/{repository}/manifests/{version}", method="HEAD",
                      headers={"Authorization": "Bearer " + token, "Accept": ACCEPT, "User-Agent": "innopool-v2-runtime-images/2.0"})
    try:
        with opener(request, timeout=timeout) as response:
            digest = response.headers.get("Docker-Content-Digest", "")
    except HTTPError as error:
        if error.code in (401, 403, 404):
            return None
        raise
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RegistryError("registry returned no content digest for the runtime tag")
    return digest[len("sha256:"):]


def record(database, challenge_id, name, version, image):
    """Append the pinned image when it differs from the newest record; True when a row was added."""
    if not CHALLENGE.fullmatch(challenge_id) or not NAME.fullmatch(name):
        raise ProtocolDataError("runtime image records need a challenge id and its lowercase name")
    check_version(version)
    match = IMAGE.fullmatch(image)
    if match is None or match.group(1) != name:
        raise ProtocolDataError("a runtime image must be the official pinned digest of the named challenge")
    with database.transaction() as cursor:
        cursor.execute("""SELECT challenge_name, image FROM runtime_images WHERE challenge_id=%s AND runtime_version=%s
            ORDER BY resolved_at DESC, id DESC LIMIT 1""", (challenge_id, version))
        newest = cursor.fetchone()
        if newest and newest["challenge_name"] == name and newest["image"] == image:
            return False
        cursor.execute("""INSERT INTO runtime_images(id, challenge_id, challenge_name, runtime_version, image)
            VALUES (%s,%s,%s,%s,%s)""", (str(uuid.uuid4()), challenge_id, name, version, image))
        return True


def rows(database, version):
    """The newest pinned image per challenge at this runtime version, oldest challenge id first."""
    check_version(version)
    with database.transaction() as cursor:
        cursor.execute("""SELECT DISTINCT ON (challenge_id) challenge_id, challenge_name, image, resolved_at
            FROM runtime_images WHERE runtime_version=%s ORDER BY challenge_id, resolved_at DESC, id DESC""", (version,))
        return [dict(row) for row in cursor.fetchall()]


def current(database, version):
    """Challenge id to pinned image at this runtime version."""
    return {row["challenge_id"]: row["image"] for row in rows(database, version)}


def latest(database, challenge_id):
    """The newest image ever pinned for a challenge at any version, or None."""
    with database.transaction() as cursor:
        cursor.execute("SELECT image FROM runtime_images WHERE challenge_id=%s ORDER BY resolved_at DESC, id DESC LIMIT 1",
                       (challenge_id,))
        row = cursor.fetchone()
        return row["image"] if row else None


class Resolver:
    """Keeps the pinned image per active challenge current, at most once per interval."""

    def __init__(self, database, version, *, interval=3600, resolve=resolve_digest, clock=time.monotonic):
        self.database, self.version = database, check_version(version)
        if type(interval) is not int or not 1 <= interval <= 86400:
            raise ValueError("runtime image refresh interval must be an integer of seconds between 1 and 86400")
        self.interval, self.resolve, self.clock = interval, resolve, clock
        self.checked = None

    def due(self):
        return self.checked is None or self.clock() - self.checked >= self.interval

    def current(self):
        return current(self.database, self.version)

    def refresh(self, snapshot):
        """Pin the registry's current image for every active challenge of the snapshot.

        A challenge whose runtime is not published at this version, or whose
        lookup fails, keeps whatever was recorded before; the outcome names it.
        """
        self.checked = self.clock()
        outcome = {"runtime_version": self.version, "pinned": [], "changed": [], "unpublished": [], "failed": []}
        for challenge_id in sorted(snapshot.challenges):
            config = snapshot.challenges[challenge_id].get("config") or {}
            name = config.get("name")
            if not isinstance(name, str) or not NAME.fullmatch(name):
                log.warning("challenge %s has no usable name in its TIG configuration", challenge_id)
                outcome["failed"].append(challenge_id)
                continue
            try:
                digest = self.resolve(name, self.version)
            except Exception:
                log.exception("runtime image lookup failed for %s (%s)", challenge_id, name)
                outcome["failed"].append(challenge_id)
                continue
            if digest is None:
                outcome["unpublished"].append(challenge_id)
                continue
            if record(self.database, challenge_id, name, self.version, reference(name, digest)):
                outcome["changed"].append(challenge_id)
                log.info("pinned %s runtime %s for %s", name, self.version, challenge_id)
            outcome["pinned"].append(challenge_id)
        return outcome
