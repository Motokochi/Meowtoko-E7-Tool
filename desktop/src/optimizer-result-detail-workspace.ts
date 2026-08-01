import type { OptimizerResultDetailSnapshot } from './shared/optimizer-result-detail';

export interface OptimizerResultDetailWorkspaceState {
  snapshot: OptimizerResultDetailSnapshot | null;
  open: boolean;
  pending: boolean;
  error: string | null;
}

export const initialOptimizerResultDetailWorkspaceState: OptimizerResultDetailWorkspaceState = {
  snapshot: null,
  open: false,
  pending: false,
  error: null,
};

export type OptimizerResultDetailWorkspaceAction =
  | { type: 'session-reset' }
  | { type: 'selection-started'; rowKey: string }
  | { type: 'snapshot-received'; snapshot: OptimizerResultDetailSnapshot }
  | { type: 'selection-failed'; message: string }
  | { type: 'local-equip-completed'; heroName: string }
  | { type: 'closed' };

export function optimizerResultDetailWorkspaceReducer(
  state: OptimizerResultDetailWorkspaceState,
  action: OptimizerResultDetailWorkspaceAction,
): OptimizerResultDetailWorkspaceState {
  switch (action.type) {
    case 'session-reset':
      return initialOptimizerResultDetailWorkspaceState;
    case 'selection-started':
      return { ...state, open: true, pending: true, error: null };
    case 'snapshot-received':
      if (state.snapshot && action.snapshot.sequence <= state.snapshot.sequence) return state;
      if (action.snapshot.state === 'idle') return initialOptimizerResultDetailWorkspaceState;
      return { ...state, snapshot: action.snapshot, pending: false, error: null };
    case 'selection-failed':
      return { ...state, open: true, pending: false, error: action.message };
    case 'local-equip-completed':
      if (state.snapshot?.state !== 'completed' || !state.snapshot.detail) return state;
      return {
        ...state,
        snapshot: {
          ...state.snapshot,
          detail: {
            ...state.snapshot.detail,
            equippedCount: 6,
            gear: state.snapshot.detail.gear.map((item) => ({
              ...item,
              equippedStatus: 'selected-hero',
              equippedHeroName: action.heroName,
            })),
          },
        },
      };
    case 'closed':
      return { ...state, open: false, pending: false, error: null };
  }
}
