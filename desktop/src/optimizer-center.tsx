import { OptimizerProfileEditor } from './optimizer-profile-editor';
import { OptimizerResultExplorer } from './optimizer-result-explorer';
import type { OptimizerResultWorkspaceState } from './optimizer-result-workspace';
import {
  initialOptimizerResultDetailWorkspaceState,
  type OptimizerResultDetailWorkspaceState,
} from './optimizer-result-detail-workspace';
import type { OptimizerResultQuery } from './shared/optimizer-results';
import type { OptimizerResultDetailRequest } from './shared/optimizer-result-detail';
import type {
  OptimizerResultExportFormat,
  OptimizerResultExportSnapshot,
} from './shared/optimizer-result-export';
import { OptimizerSearchStatus } from './optimizer-search-status';
import type { OptimizerSearchWorkspaceState } from './optimizer-search-workspace';
import {
  initialOptimizerProfileWorkspaceState,
  type OptimizerProfileWorkspaceState,
} from './optimizer-profile-workspace';
import type { OptimizerInventorySnapshot } from './shared/optimizer-inventory';
import type { OptimizerArtifactSummary, OptimizerHeroDraft } from './shared/optimizer-profile';

interface OptimizerCenterProps {
  inventory: OptimizerInventorySnapshot;
  profile?: OptimizerProfileWorkspaceState;
  onArtifactSearch?(query: string): void;
  onChooseArtifact?(artifact: OptimizerArtifactSummary | null): void;
  onDraftChange?(draft: OptimizerHeroDraft): void;
  onHeroSearch?(query: string): void;
  onSaveDraft?(): void;
  onSelectHero?(heroId: string): void;
  search?: OptimizerSearchWorkspaceState;
  onStartSearch?(): void;
  onCancelSearch?(jobId: string): void;
  onRetrySearchWithCpu?(jobId: string): void;
  results?: OptimizerResultWorkspaceState;
  onQueryResults?(query: OptimizerResultQuery): void;
  onCancelResults?(queryId: string): void;
  resultDetail?: OptimizerResultDetailWorkspaceState;
  onInspectResult?(request: OptimizerResultDetailRequest): void;
  optimizerEquipping?: boolean;
  onEquipResult?(request: OptimizerResultDetailRequest): void;
  onCloseResultDetail?(): void;
  resultExport?: OptimizerResultExportSnapshot | null;
  onExportResults?(runId: string, queryId: string, format: OptimizerResultExportFormat): void;
  onCancelResultExport?(exportId: string): void;
}

export function OptimizerCenter({
  inventory,
  profile = initialOptimizerProfileWorkspaceState,
  onArtifactSearch = () => undefined,
  onChooseArtifact = () => undefined,
  onDraftChange = () => undefined,
  onHeroSearch = () => undefined,
  onSaveDraft = () => undefined,
  onSelectHero = () => undefined,
  search = { snapshot: null, pending: false, error: null },
  onStartSearch = () => undefined,
  onCancelSearch = () => undefined,
  onRetrySearchWithCpu = () => undefined,
  results = { options: null, snapshot: null, pending: false, error: null, activeQuery: null },
  onQueryResults = () => undefined,
  onCancelResults = () => undefined,
  resultDetail = initialOptimizerResultDetailWorkspaceState,
  onInspectResult = () => undefined,
  optimizerEquipping = false,
  onEquipResult = () => undefined,
  onCloseResultDetail = () => undefined,
  resultExport = null,
  onExportResults = () => undefined,
  onCancelResultExport = () => undefined,
}: OptimizerCenterProps): React.JSX.Element {
  const hasInventory = inventory.state === 'ready';
  const searchIssues = profile.envelope ? profile.issues : [];
  const searchDisabled = !hasInventory
    || !profile.envelope
    || profile.loading
    || profile.saving
    || searchIssues.length > 0;
  const searchDisabledReason = !hasInventory
    ? 'Import owned gear in the Importer workspace before searching.'
    : !profile.envelope
      ? 'Choose a character before searching.'
      : profile.loading || profile.saving
        ? 'Wait for the character profile action to finish.'
        : searchIssues.length > 0
          ? 'Correct the highlighted build settings before searching.'
          : '';

  return (
    <div className="page-stack optimizer-page optimizer-workbench-page">
      <OptimizerProfileEditor
        enabled={hasInventory}
        onArtifactSearch={onArtifactSearch}
        onChooseArtifact={onChooseArtifact}
        onDraftChange={onDraftChange}
        onHeroSearch={onHeroSearch}
        onSaveDraft={onSaveDraft}
        onSelectHero={onSelectHero}
        profile={profile}
      />

      <OptimizerSearchStatus
        disabled={searchDisabled}
        disabledReason={searchDisabledReason}
        error={search.error}
        onCancel={onCancelSearch}
        onRetryCpu={onRetrySearchWithCpu}
        onStart={onStartSearch}
        pending={search.pending}
        snapshot={search.snapshot}
      />

      <OptimizerResultExplorer
        detail={resultDetail}
        equipping={optimizerEquipping}
        error={results.error}
        exportSnapshot={resultExport}
        initialQuery={results.activeQuery}
        onCancel={onCancelResults}
        onCancelExport={onCancelResultExport}
        onCloseDetail={onCloseResultDetail}
        onExport={onExportResults}
        onInspect={onInspectResult}
        onEquip={onEquipResult}
        onQuery={onQueryResults}
        options={results.options}
        pending={results.pending}
        runId={search.snapshot?.state === 'completed' ? search.snapshot.resultRunId : null}
        heroName={profile.details?.hero.name}
        snapshot={results.snapshot}
      />
    </div>
  );
}
