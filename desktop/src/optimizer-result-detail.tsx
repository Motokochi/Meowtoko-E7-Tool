import { useEffect, useRef, useState } from 'react';

import armorIcon from '../../assets/equipment/slots/geararmor.png';
import bootsIcon from '../../assets/equipment/slots/gearboots.png';
import helmetIcon from '../../assets/equipment/slots/gearhelmet.png';
import necklaceIcon from '../../assets/equipment/slots/gearnecklace.png';
import ringIcon from '../../assets/equipment/slots/gearring.png';
import weaponIcon from '../../assets/equipment/slots/gearweapon.png';
import { characterArtworkUrl } from './shared/character-artwork';
import type { OptimizerResultDetailWorkspaceState } from './optimizer-result-detail-workspace';
import type {
  OptimizerOwnedGearDetail,
  OptimizerResultBuildDetail,
  OptimizerResultDetailRequest,
} from './shared/optimizer-result-detail';
import { Alert, Badge, Button, Card, Dialog } from './ui';

interface OptimizerResultDetailProps {
  workspace: OptimizerResultDetailWorkspaceState;
  heroName?: string;
  equipping?: boolean;
  onClose(): void;
  onEquip?(request: OptimizerResultDetailRequest): void;
}

function formatGearValue(statId: string, value: number): string {
  const suffix = statId.endsWith('_percent') ? '%' : '';
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${suffix}`;
}

function equippedLabel(status: OptimizerOwnedGearDetail['equippedStatus']): string {
  if (status === 'selected-hero') return 'Already equipped';
  if (status === 'other-hero') return 'On another hero';
  return 'Unequipped';
}

function equippedTone(
  status: OptimizerOwnedGearDetail['equippedStatus'],
): 'success' | 'warning' | 'neutral' {
  if (status === 'selected-hero') return 'success';
  if (status === 'other-hero') return 'warning';
  return 'neutral';
}

export const GEAR_SLOT_ICONS: Readonly<Record<string, string>> = {
  'slot.weapon': weaponIcon,
  'slot.helmet': helmetIcon,
  'slot.armor': armorIcon,
  'slot.necklace': necklaceIcon,
  'slot.ring': ringIcon,
  'slot.boots': bootsIcon,
};

export function EquippedHeroFace({
  heroName,
}: {
  heroName: string | null;
}): React.JSX.Element {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [heroName]);

  const label = heroName
    ? `Equipped by ${heroName}`
    : 'Equipped character artwork unavailable';
  return (
    <span className="optimizer-gear-owner-face" title={label}>
      {heroName && !failed ? (
        <img
          alt={`${heroName} equipped character`}
          onError={() => setFailed(true)}
          src={characterArtworkUrl(heroName, 'face_s')}
        />
      ) : (
        <span aria-label={label} role="img">?</span>
      )}
    </span>
  );
}

export function GearCard({
  gear,
  inventory = false,
}: {
  gear: OptimizerOwnedGearDetail;
  inventory?: boolean;
}): React.JSX.Element {
  const equipped = gear.equippedStatus !== 'unequipped';
  return (
    <li className="optimizer-gear-card" data-equipped={gear.equippedStatus}>
      <header>
        <span className="optimizer-gear-slot-icon">
          <img alt={`${gear.slotLabel} slot`} src={GEAR_SLOT_ICONS[gear.slotId]} />
        </span>
        <div className="optimizer-gear-level">
          <strong>+{gear.enhance}</strong>
          <span>Lv {gear.itemLevel} · {gear.rankLabel}</span>
        </div>
        {equipped && <EquippedHeroFace heroName={gear.equippedHeroName} />}
        <span className="optimizer-gear-set" title={`${gear.setLabel} set`}>
          <i aria-hidden="true" />{gear.setLabel}
        </span>
      </header>

      <div className="optimizer-gear-main-stat">
        <span>{gear.mainStat.label}</span>
        <strong>{formatGearValue(gear.mainStat.statId, gear.mainStat.value)}</strong>
      </div>

      <dl className="optimizer-gear-substats">
        {gear.substats.map((stat) => (
          <div key={stat.statId}>
            <dt>{stat.label}</dt>
            <dd>{formatGearValue(stat.statId, stat.value)}</dd>
          </div>
        ))}
      </dl>

      <footer>
        <strong title="Gear score">GS {gear.gearScore}</strong>
        <Badge tone={equippedTone(gear.equippedStatus)}>
          {inventory && equipped ? 'Equipped' : equippedLabel(gear.equippedStatus)}
        </Badge>
        <span>{gear.locked ? 'Locked' : 'Unlocked'}</span>
      </footer>
    </li>
  );
}

function CompletedDetail({ detail }: { detail: OptimizerResultBuildDetail }): React.JSX.Element {
  return (
    <>
      <div className="optimizer-gear-build-summary">
        <Badge tone="success">Exact build</Badge>
        <span><strong>{detail.priorityScore.toLocaleString()}</strong> priority</span>
        <span><strong>{detail.equippedCount}/6</strong> already equipped</span>
        <span>{detail.sets.map((set) => `${set.label} ×${set.pieces}`).join(' · ')}</span>
      </div>

      <section aria-labelledby="optimizer-detail-gear-title">
        <div className="optimizer-detail-section-heading">
          <div>
            <h3 id="optimizer-detail-gear-title">Equip these six pieces</h3>
            <p>Every card is a piece currently stored in your imported inventory.</p>
          </div>
          <Badge tone="success">Sets complete</Badge>
        </div>
        <ol className="optimizer-gear-card-grid">
          {detail.gear.map((gear) => <GearCard gear={gear} key={gear.gearKey} />)}
        </ol>
      </section>
    </>
  );
}

export function OptimizerResultDetail({
  workspace,
  heroName = 'the selected character',
  equipping = false,
  onClose,
  onEquip = () => undefined,
}: OptimizerResultDetailProps): React.JSX.Element | null {
  const panelRef = useRef<HTMLElement | null>(null);
  const [confirmEquip, setConfirmEquip] = useState(false);
  useEffect(() => {
    if (!workspace.open) return undefined;
    const frame = window.requestAnimationFrame(() => panelRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [workspace.open]);
  useEffect(() => {
    setConfirmEquip(false);
  }, [workspace.open, workspace.snapshot?.rowKey]);
  if (!workspace.open) return null;
  const snapshot = workspace.snapshot;
  const equipRequest = snapshot?.state === 'completed' && snapshot.runId && snapshot.queryId && snapshot.rowKey
    ? { runId: snapshot.runId, queryId: snapshot.queryId, rowKey: snapshot.rowKey }
    : null;
  return (
    <aside
      aria-busy={workspace.pending || snapshot?.state === 'loading' || undefined}
      aria-describedby="optimizer-result-detail-description"
      aria-labelledby="optimizer-result-detail-title"
      className="optimizer-result-detail"
      ref={panelRef}
      role="region"
      tabIndex={-1}
    >
      <Card elevated>
        <div className="section-heading optimizer-result-detail-heading">
          <div>
            <span className="card-kicker">SELECTED BUILD</span>
            <h2 id="optimizer-result-detail-title">Recommended gear</h2>
            <p id="optimizer-result-detail-description">A compact card view of the exact owned pieces to equip.</p>
          </div>
          <div className="optimizer-result-detail-actions">
            {equipRequest && (
              <Button busy={equipping} onClick={() => setConfirmEquip(true)} type="button">
                Equip
              </Button>
            )}
            <Button disabled={equipping} onClick={onClose} type="button" variant="secondary">Close cards</Button>
          </div>
        </div>
        <Dialog
          description={`Assign these six pieces to ${heroName} inside Meowtoko E7 Tool.`}
          footer={(
            <>
              <Button disabled={equipping} onClick={() => setConfirmEquip(false)} type="button" variant="secondary">Cancel</Button>
              <Button
                busy={equipping}
                onClick={() => {
                  if (!equipRequest) return;
                  setConfirmEquip(false);
                  onEquip(equipRequest);
                }}
                type="button"
              >
                Equip build
              </Button>
            </>
          )}
          onClose={() => {
            if (!equipping) setConfirmEquip(false);
          }}
          open={confirmEquip && equipRequest !== null}
          title="Equip this build locally?"
        >
          <p>
            Meowtoko E7 Tool will unequip the character&apos;s current local build and move any
            selected pieces assigned to other heroes. This does not tap or change
            Epic Seven. Import a fresh gear.txt after changing equipment in the game.
          </p>
        </Dialog>
        {(workspace.pending || snapshot?.state === 'loading') && (
          <div aria-live="polite" className="optimizer-result-loading" role="status">
            <span className="spinner" /><span>Loading the selected gear cards…</span>
          </div>
        )}
        {workspace.error && <Alert title="Build detail unavailable" tone="danger">{workspace.error} Close the cards, then select another visible build.</Alert>}
        {snapshot?.state === 'failed' && snapshot.failure && <Alert title="Build detail stopped safely" tone="danger">{snapshot.failure.message} Close the cards, then select another visible build.</Alert>}
        {snapshot?.state === 'completed' && snapshot.detail && <CompletedDetail detail={snapshot.detail} />}
      </Card>
    </aside>
  );
}
