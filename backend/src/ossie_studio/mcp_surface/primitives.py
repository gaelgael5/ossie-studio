"""Registre des primitives : pour chaque identifiant du catalogue, ses arguments
et son code. Un test vérifie que catalogue et registre concordent."""

from __future__ import annotations

from collections.abc import Mapping

from ossie_studio.mcp_surface import application as app_map
from ossie_studio.mcp_surface import governance_writes as gov
from ossie_studio.mcp_surface import preparation as prep
from ossie_studio.mcp_surface import reads, references, source_writes, writes
from ossie_studio.mcp_surface import referentiels as refs
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
    # Préparer la publication (brouillon de la personne)
    "get_merge_conflicts": Implementation(prep.MergeConflictsArgs, prep.get_merge_conflicts),
    "resolve_merge": Implementation(prep.ResolveMergeArgs, prep.resolve_merge),
    "auto_merge": Implementation(BranchArgs, prep.auto_merge),
    "list_impacts": Implementation(BranchArgs, prep.list_impacts),
    "verify_impact": Implementation(prep.ImpactArgs, prep.verify_impact),
    "get_validation_log": Implementation(BranchArgs, prep.get_validation_log),
    "accept_validation_warning": Implementation(prep.WarningArgs, prep.accept_validation_warning),
    "list_sources": Implementation(ViewArgs, reads.list_sources),
    "list_raw_schemas": Implementation(SourceArgs, reads.list_raw_schemas),
    "read_raw_schema": Implementation(ObjectArgs, reads.read_raw_schema),
    "get_reference_graph": Implementation(
        references.ReferenceGraphArgs, references.get_reference_graph
    ),
    # Cartographie applicative (ticket 72ce32b3)
    "list_modules": Implementation(SourceArgs, app_map.list_modules),
    "get_module": Implementation(app_map.ModuleArgs, app_map.get_module),
    "get_functions": Implementation(ObjectArgs, app_map.get_functions),
    "list_entry_points": Implementation(app_map.EntryPointsArgs, app_map.list_entry_points),
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
    "list_contracts": Implementation(SourceArgs, source_writes.list_contracts),
    "deposit_contract": Implementation(
        source_writes.DepositContractArgs, source_writes.deposit_contract
    ),
    "remove_contract": Implementation(
        source_writes.ContractVersionArgs, source_writes.remove_contract
    ),
    # Référentiels (version publiée, même lecture que l'API)
    "list_referentiels": Implementation(refs.SpaceArgs, refs.list_referentiels),
    "read_referentiel_model": Implementation(refs.ReferentielArgs, refs.read_referentiel_model),
    "search_referentiel_values": Implementation(
        refs.SearchValuesArgs, refs.search_referentiel_values
    ),
    "get_referentiel_value": Implementation(refs.ValueByKeyArgs, refs.get_referentiel_value),
    # Gouvernance
    "list_certifications": Implementation(ViewArgs, reads.list_certifications),
    "create_tag": Implementation(gov.CreateTagArgs, gov.create_tag),
    "update_tag": Implementation(gov.UpdateTagArgs, gov.update_tag),
    "delete_tag": Implementation(gov.TagArgs, gov.delete_tag),
    "create_classification": Implementation(
        gov.CreateClassificationArgs, gov.create_classification
    ),
    "update_classification": Implementation(
        gov.UpdateClassificationArgs, gov.update_classification
    ),
    "delete_classification": Implementation(gov.SchemeArgs, gov.delete_classification),
    "create_level": Implementation(gov.CreateLevelArgs, gov.create_level),
    "update_level": Implementation(gov.UpdateLevelArgs, gov.update_level),
    "delete_level": Implementation(gov.LevelArgs, gov.delete_level),
    "reorder_levels": Implementation(gov.ReorderLevelsArgs, gov.reorder_levels),
    "create_certification": Implementation(gov.CreateCertificationArgs, gov.create_certification),
    "update_certification": Implementation(gov.UpdateCertificationArgs, gov.update_certification),
    "delete_certification": Implementation(gov.CertificationArgs, gov.delete_certification),
}
