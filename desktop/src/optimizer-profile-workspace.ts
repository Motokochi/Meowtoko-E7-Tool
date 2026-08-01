import {
  validateOptimizerHeroDraft,
  type OptimizerArtifactSearchResult,
  type OptimizerArtifactSummary,
  type OptimizerDraftValidationIssue,
  type OptimizerHeroDetails,
  type OptimizerHeroDraft,
  type OptimizerHeroDraftEnvelope,
  type OptimizerHeroSummary,
} from './shared/optimizer-profile';

export interface OptimizerProfileNotice {
  tone: 'info' | 'success' | 'warning' | 'danger';
  title: string;
  message: string;
}

export interface OptimizerProfileWorkspaceState {
  heroQuery: string;
  heroResults: OptimizerHeroSummary[];
  heroSearching: boolean;
  artifactQuery: string;
  artifactResults: OptimizerArtifactSummary[];
  artifactSearching: boolean;
  details: OptimizerHeroDetails | null;
  envelope: OptimizerHeroDraftEnvelope | null;
  loading: boolean;
  saving: boolean;
  dirty: boolean;
  issues: OptimizerDraftValidationIssue[];
  notice: OptimizerProfileNotice | null;
}

export const initialOptimizerProfileWorkspaceState: OptimizerProfileWorkspaceState = {
  heroQuery: '',
  heroResults: [],
  heroSearching: false,
  artifactQuery: '',
  artifactResults: [],
  artifactSearching: false,
  details: null,
  envelope: null,
  loading: false,
  saving: false,
  dirty: false,
  issues: [],
  notice: null,
};

export type OptimizerProfileWorkspaceAction =
  | { type: 'async-reset' }
  | { type: 'data-reset' }
  | { type: 'hero-search-started'; query: string }
  | { type: 'hero-search-completed'; query: string; results: OptimizerHeroSummary[] }
  | { type: 'hero-search-failed'; query: string; message: string }
  | { type: 'artifact-search-started'; query: string }
  | { type: 'artifact-search-completed'; result: OptimizerArtifactSearchResult }
  | { type: 'artifact-search-failed'; query: string; message: string }
  | { type: 'selection-started' }
  | { type: 'selection-completed'; details: OptimizerHeroDetails; envelope: OptimizerHeroDraftEnvelope }
  | { type: 'selection-failed'; message: string }
  | { type: 'draft-updated'; draft: OptimizerHeroDraft }
  | { type: 'artifact-selected'; artifact: OptimizerArtifactSummary | null }
  | { type: 'save-started' }
  | { type: 'save-completed'; envelope: OptimizerHeroDraftEnvelope }
  | { type: 'save-failed'; message: string; issues?: OptimizerDraftValidationIssue[] };

export function optimizerProfileWorkspaceReducer(
  state: OptimizerProfileWorkspaceState,
  action: OptimizerProfileWorkspaceAction,
): OptimizerProfileWorkspaceState {
  switch (action.type) {
    case 'data-reset':
      return initialOptimizerProfileWorkspaceState;
    case 'async-reset':
      return {
        ...state,
        heroSearching: false,
        artifactSearching: false,
        loading: false,
        saving: false,
        notice: null,
      };
    case 'hero-search-started':
      return { ...state, heroQuery: action.query, heroSearching: true };
    case 'hero-search-completed':
      return { ...state, heroQuery: action.query, heroResults: action.results, heroSearching: false };
    case 'hero-search-failed':
      return {
        ...state,
        heroQuery: action.query,
        heroSearching: false,
        notice: { tone: 'danger', title: 'Hero search failed', message: action.message },
      };
    case 'artifact-search-started':
      return { ...state, artifactQuery: action.query, artifactSearching: true };
    case 'artifact-search-completed':
      return { ...state, artifactQuery: action.result.query, artifactResults: action.result.results, artifactSearching: false };
    case 'artifact-search-failed':
      return {
        ...state,
        artifactQuery: action.query,
        artifactSearching: false,
        notice: { tone: 'danger', title: 'Artifact search failed', message: action.message },
      };
    case 'selection-started':
      return { ...state, loading: true, issues: [], notice: null };
    case 'selection-completed': {
      const selected = action.envelope.selectedArtifact;
      return {
        ...state,
        details: action.details,
        envelope: action.envelope,
        heroQuery: action.details.hero.name,
        artifactQuery: selected?.name ?? '',
        artifactResults: selected ? [selected] : [],
        loading: false,
        saving: false,
        dirty: false,
        issues: [],
        notice: {
          tone: 'info',
          title: action.envelope.state === 'saved' ? 'Saved hero draft restored' : 'New hero defaults loaded',
          message: action.envelope.state === 'saved'
            ? 'This hero’s independent profile, targets, and search settings were restored.'
            : 'Level 60, six-star, fully-awakened defaults are ready to edit.',
        },
      };
    }
    case 'selection-failed':
      return {
        ...state,
        loading: false,
        saving: false,
        notice: { tone: 'danger', title: 'Hero could not be opened', message: action.message },
      };
    case 'draft-updated':
      if (!state.envelope) return state;
      return {
        ...state,
        envelope: { ...state.envelope, draft: action.draft },
        dirty: true,
        issues: validateOptimizerHeroDraft(action.draft, state.details),
        notice: null,
      };
    case 'artifact-selected': {
      if (!state.envelope) return state;
      const draft = {
        ...state.envelope.draft,
        artifact: action.artifact
          ? { artifactId: action.artifact.artifactId, level: 30, attackOverride: null, healthOverride: null, defenseOverride: null }
          : { artifactId: null, level: null, attackOverride: null, healthOverride: null, defenseOverride: null },
      };
      return {
        ...state,
        artifactQuery: action.artifact?.name ?? '',
        artifactResults: action.artifact ? [action.artifact] : [],
        envelope: { ...state.envelope, draft, selectedArtifact: action.artifact },
        dirty: true,
        issues: validateOptimizerHeroDraft(draft, state.details),
        notice: null,
      };
    }
    case 'save-started':
      return { ...state, saving: true, issues: [], notice: null };
    case 'save-completed':
      return {
        ...state,
        envelope: action.envelope,
        saving: false,
        dirty: false,
        issues: [],
        notice: { tone: 'success', title: 'Hero draft saved', message: 'This hero’s profile, targets, and search settings are stored independently.' },
      };
    case 'save-failed':
      return {
        ...state,
        saving: false,
        issues: action.issues ?? [],
        notice: { tone: 'danger', title: 'Hero draft was not saved', message: action.message },
      };
  }
}
