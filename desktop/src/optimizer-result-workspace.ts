import type { OptimizerResultOptions, OptimizerResultQuery, OptimizerResultSnapshot } from './shared/optimizer-results';

export interface OptimizerResultWorkspaceState {
  options: OptimizerResultOptions | null;
  snapshot: OptimizerResultSnapshot | null;
  pending: boolean;
  error: string | null;
  activeQuery: OptimizerResultQuery | null;
}

export const initialOptimizerResultWorkspaceState: OptimizerResultWorkspaceState = {
  options: null,
  snapshot: null,
  pending: false,
  error: null,
  activeQuery: null,
};

export type OptimizerResultWorkspaceAction =
  | { type: 'session-reset' }
  | { type: 'options-received'; options: OptimizerResultOptions }
  | { type: 'command-started' }
  | { type: 'query-started'; query: OptimizerResultQuery }
  | { type: 'snapshot-received'; snapshot: OptimizerResultSnapshot }
  | { type: 'command-failed'; message: string };

export function optimizerResultWorkspaceReducer(
  state: OptimizerResultWorkspaceState,
  action: OptimizerResultWorkspaceAction,
): OptimizerResultWorkspaceState {
  switch (action.type) {
    case 'session-reset':
      return { ...initialOptimizerResultWorkspaceState, options: state.options };
    case 'options-received':
      return { ...state, options: action.options };
    case 'command-started':
      return { ...state, pending: true, error: null };
    case 'query-started':
      return { ...state, activeQuery: action.query, pending: true, error: null };
    case 'snapshot-received':
      if (state.snapshot && action.snapshot.sequence <= state.snapshot.sequence) return state;
      return { ...state, snapshot: action.snapshot, pending: false, error: null };
    case 'command-failed':
      return { ...state, pending: false, error: action.message };
  }
}
