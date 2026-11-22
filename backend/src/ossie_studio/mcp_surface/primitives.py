"""Registre des primitives : pour chaque identifiant du catalogue, ses arguments
et son code. Un test vérifie que catalogue et registre concordent."""

from __future__ import annotations

from collections.abc import Mapping

from ossie_studio.mcp_surface import reads, writes
from ossie_studio.mcp_surface.branch import BranchArgs, ObjectArgs, SourceArgs, ViewArgs
from ossie_studio.mcp_surface.core import (
    Implementation,
    NoArgs,
    PrimitiveContext,
    PrimitiveError,
)

__all__ = ["IMPLEMENTATIONS", "PrimitiveContext", "PrimitiveError"]

IMPLEMENTATIONS: Mapping[str, Implementation] = {
    "list_spaces": Implementation(NoArgs, reads.list_spaces),
    "list_versions": Implementation(BranchArgs, reads.list_versions),
    "get_my_draft": Implementation(BranchArgs, reads.get_my_draft),
    "get_publish_readiness": Implementation(BranchArgs, reads.get_publish_readiness),
    "list_sources": Implementation(ViewArgs, reads.list_sources),
    "list_raw_schemas": Implementation(SourceArgs, reads.list_raw_schemas),
    "read_raw_schema": Implementation(ObjectArgs, reads.read_raw_schema),
    "list_assets": Implementation(SourceArgs, reads.list_assets),
    "read_asset": Implementation(ObjectArgs, reads.read_asset),
    "get_tri_rules": Implementation(SourceArgs, reads.get_tri_rules),
    "get_review": Implementation(SourceArgs, reads.get_review),
    "list_tags": Implementation(ViewArgs, reads.list_tags),
    "list_classifications": Implementation(ViewArgs, reads.list_classifications),
    # Écriture (brouillon de la personne)
    "retain_asset": Implementation(writes.RetainArgs, writes.retain_asset),
    "remove_asset": Implementation(writes.ObjectWriteArgs, writes.remove_asset),
    "enrich_asset": Implementation(writes.EnrichArgs, writes.enrich_asset),
    "refresh_assets": Implementation(writes.RefreshArgs, writes.refresh_assets),
    "set_tri_rules": Implementation(writes.SetTriRulesArgs, writes.set_tri_rules),
    "apply_review": Implementation(writes.ApplyReviewArgs, writes.apply_review),
}
