"""Controller-owned typed JSON artifacts; never a general filesystem store."""

import hashlib
import os
import stat
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from syndicate.cli_envelope import ArtifactKind, ArtifactRef, Command

Model = TypeVar("Model", bound=BaseModel)


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root.resolve() != root:
            raise ValueError("Controller root must be absolute and nonsymlink")
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, reference: ArtifactRef) -> Path:
        return (
            self._root
            / "runs"
            / str(reference.operation_id)
            / str(reference.attempt_id)
            / f"{reference.kind.value}.json"
        )

    def write(
        self, command: Command, kind: ArtifactKind, value: BaseModel
    ) -> ArtifactRef:
        path = (
            self._root
            / "runs"
            / str(command.operation_id)
            / str(command.attempt_id)
            / f"{kind.value}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = value.model_dump_json().encode()
        with path.open("xb") as artifact:
            artifact.write(payload)
        return ArtifactRef(
            kind=kind,
            operation_id=command.operation_id,
            attempt_id=command.attempt_id,
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    def load(self, reference: ArtifactRef, model: type[Model]) -> Model:
        path = self.path_for(reference)
        if not path.is_relative_to(self._root / "runs") or path.is_symlink():
            raise ValueError("Artifact path is invalid")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("Artifact must be a regular file")
            with os.fdopen(descriptor, "rb") as artifact:
                descriptor = -1
                payload = artifact.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if hashlib.sha256(payload).hexdigest() != reference.sha256:
            raise ValueError("Artifact hash does not match reference")
        return model.model_validate_json(payload)
