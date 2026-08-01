import type { OptimizerSearchSnapshot } from './shared/optimizer-search';

export interface OptimizerSearchWorkspaceState {
  snapshot: OptimizerSearchSnapshot | null;
  pending: boolean;
  error: string | null;
}

export const initialOptimizerSearchWorkspaceState: OptimizerSearchWorkspaceState = {
  snapshot: null,
  pending: false,
  error: null,
};

export type OptimizerSearchWorkspaceAction =
  | { type: 'session-reset' }
  | { type: 'command-started' }
  | { type: 'snapshot-received'; snapshot: OptimizerSearchSnapshot }
  | { type: 'command-failed'; message: string };

export function optimizerSearchWorkspaceReducer(
  state: OptimizerSearchWorkspaceState,
  action: OptimizerSearchWorkspaceAction,
): OptimizerSearchWorkspaceState {
  switch (action.type) {
    case 'session-reset':
      return initialOptimizerSearchWorkspaceState;
    case 'command-started':
      return { ...state, pending: true, error: null };
    case 'snapshot-received':
      if (state.snapshot && action.snapshot.sequence <= state.snapshot.sequence) return state;
      return { snapshot: action.snapshot, pending: false, error: null };
    case 'command-failed':
      return { ...state, pending: false, error: action.message };
  }
}
