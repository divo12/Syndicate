from pathlib import Path
from uuid import UUID

from syndicate.lineage import HarnessLineage, PromotionStatus


def digest(char: str) -> str:
    return "sha256:" + char * 64


def test_promotion_uses_parent_compare_and_swap(tmp_path: Path) -> None:
    lineage = HarnessLineage(tmp_path / "controller.sqlite", digest("a"), digest("d"))

    receipt = lineage.promote(UUID(int=1), digest("a"), digest("b"), digest("e"))

    assert receipt.status is PromotionStatus.PROMOTED
    assert lineage.current().harness_hash == digest("b")
    assert (
        lineage.promote(UUID(int=2), digest("a"), digest("c"), digest("f")).status
        is PromotionStatus.STALE
    )
    assert lineage.current().harness_hash == digest("b")


def test_rollback_selects_a_prior_version_without_erasing_lineage(
    tmp_path: Path,
) -> None:
    lineage = HarnessLineage(tmp_path / "controller.sqlite", digest("a"), digest("d"))
    lineage.promote(UUID(int=1), digest("a"), digest("b"), digest("e"))
    lineage.promote(UUID(int=2), digest("b"), digest("c"), digest("f"))

    receipt = lineage.rollback(UUID(int=3), digest("a"))

    assert receipt.status is PromotionStatus.ROLLED_BACK
    assert lineage.current().harness_hash == digest("a")
    assert len(lineage.history()) == 3
