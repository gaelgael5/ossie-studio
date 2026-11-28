"""Registre des primitives : pour chaque identifiant du catalogue, ses arguments
et son code. Un test vérifie que catalogue et registre concordent."""

from __future__ import annotations

from collections.abc import Mapping

from ossie_studio.mcp_surface import (
    document_reads,
    document_writes,
    reads,
    references,
    schemas,
    source_writes,
    write_guide,
    writes,
)
from ossie_studio.mcp_surface import preparation as prep
from ossie_studio.mcp_surface.branch import BranchArgs, BranchParam, SourceArgs
from ossie_studio.mcp_surface.core import (
    Implementation,
    PrimitiveContext,
    PrimitiveError,
)

__all__ = ["IMPLEMENTATIONS", "PrimitiveContext", "PrimitiveError"]

IMPLEMENTATIONS: Mapping[str, Implementation] = {
    "list_spaces": Implementation(BranchParam, reads.list_spaces),
    "list_versions": Implementation(BranchArgs, reads.list_versions),
    "get_my_draft": Implementation(BranchArgs, reads.get_my_draft),
    "get_publish_readiness": Implementation(BranchArgs, reads.get_publish_readiness),
    # Préparer la publication (brouillon de la personne)
    "get_merge_conflicts": Implementation(prep.MergeConflictsArgs, prep.get_merge_conflicts),
    "resolve_merge": Implementation(prep.ResolveMergeArgs, prep.resolve_merge),
    "auto_merge": Implementation(BranchArgs, prep.auto_merge),
    "list_impacts": Implementation(BranchArgs, prep.list_impacts),
    "verify_impact": Implementation(prep.ImpactArgs, prep.verify_impact),
    "get_validation_log": Implementation(prep.ValidationLogArgs, prep.get_validation_log),
    # Lire un schéma, quel que soit son domaine (ticket 06ef8679)
    "read_schema": Implementation(schemas.ReadSchemaArgs, schemas.read_schema),
    "get_reference_graph": Implementation(
        references.ReferenceGraphArgs, references.get_reference_graph
    ),
    # Cartographie applicative (ticket 72ce32b3)
    "get_review": Implementation(SourceArgs, reads.get_review),
    # Écriture et lecture communes par type de document (epic « Primitives MCP — écriture commune »)
    "write_documents": Implementation(
        document_writes.WriteDocumentsArgs, document_writes.write_documents
    ),
    "patch_document": Implementation(
        document_writes.PatchDocumentArgs, document_writes.patch_document
    ),
    "list_documents": Implementation(
        document_reads.ListDocumentsArgs, document_reads.list_documents
    ),
    "read_document": Implementation(document_reads.ReadDocumentArgs, document_reads.read_document),
    "list_document_types": Implementation(
        write_guide.ListDocumentTypesArgs, write_guide.list_document_types
    ),
    "describe_standard": Implementation(
        write_guide.DescribeStandardArgs, write_guide.describe_standard
    ),
    # Écriture (brouillon de la personne)
    "retain_asset": Implementation(writes.RetainArgs, writes.retain_asset),
    "remove_asset": Implementation(writes.ObjectWriteArgs, writes.remove_asset),
    "enrich_asset": Implementation(writes.EnrichArgs, writes.enrich_asset),
    "refresh_assets": Implementation(writes.RefreshArgs, writes.refresh_assets),
    "apply_review": Implementation(writes.ApplyReviewArgs, writes.apply_review),
    # Sources (option 1 : réutilise l'écran des sources tel quel, ticket 1920df67)
    "declare_source": Implementation(
        source_writes.DeclareSourceArgs, source_writes.declare_source_primitive
    ),
    "update_source": Implementation(
        source_writes.UpdateSourceArgs, source_writes.update_source_primitive
    ),
    "delete_source": Implementation(writes.SourceWriteArgs, source_writes.delete_source_primitive),
    "collect_source": Implementation(source_writes.CollectSourceArgs, source_writes.collect_source),
    "get_collect_status": Implementation(writes.SourceWriteArgs, source_writes.get_collect_status),
    "deposit_contract": Implementation(
        source_writes.DepositContractArgs, source_writes.deposit_contract
    ),
    "remove_contract": Implementation(
        source_writes.ContractVersionArgs, source_writes.remove_contract
    ),
}
