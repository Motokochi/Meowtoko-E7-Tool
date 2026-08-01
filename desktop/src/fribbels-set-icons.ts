import attackIcon from '../../assets/equipment/sets/setattack.png';
import counterIcon from '../../assets/equipment/sets/setcounter.png';
import criticalIcon from '../../assets/equipment/sets/setcritical.png';
import defenseIcon from '../../assets/equipment/sets/setdefense.png';
import destructionIcon from '../../assets/equipment/sets/setdestruction.png';
import fervorIcon from '../../assets/equipment/sets/setfervor.png';
import healthIcon from '../../assets/equipment/sets/sethealth.png';
import hitIcon from '../../assets/equipment/sets/sethit.png';
import immunityIcon from '../../assets/equipment/sets/setimmunity.png';
import injuryIcon from '../../assets/equipment/sets/setinjury.png';
import lifestealIcon from '../../assets/equipment/sets/setlifesteal.png';
import penetrationIcon from '../../assets/equipment/sets/setpenetration.png';
import protectionIcon from '../../assets/equipment/sets/setprotection.png';
import pursuitIcon from '../../assets/equipment/sets/setpursuit.png';
import rageIcon from '../../assets/equipment/sets/setrage.png';
import resistIcon from '../../assets/equipment/sets/setresist.png';
import revengeIcon from '../../assets/equipment/sets/setrevenge.png';
import reversalIcon from '../../assets/equipment/sets/setreversal.png';
import riposteIcon from '../../assets/equipment/sets/setriposte.png';
import speedIcon from '../../assets/equipment/sets/setspeed.png';
import torrentIcon from '../../assets/equipment/sets/settorrent.png';
import unityIcon from '../../assets/equipment/sets/setunity.png';
import warfareIcon from '../../assets/equipment/sets/setwarfare.png';
import weakeningIcon from '../../assets/equipment/sets/setweakening.png';

import type { OptimizerResultSetSummary } from './shared/optimizer-results';

export const FRIBBELS_SET_ICONS: Readonly<Record<string, string>> = Object.freeze({
  'set.health': healthIcon,
  'set.defense': defenseIcon,
  'set.attack': attackIcon,
  'set.speed': speedIcon,
  'set.critical': criticalIcon,
  'set.hit': hitIcon,
  'set.destruction': destructionIcon,
  'set.lifesteal': lifestealIcon,
  'set.counter': counterIcon,
  'set.resist': resistIcon,
  'set.unity': unityIcon,
  'set.rage': rageIcon,
  'set.immunity': immunityIcon,
  'set.penetration': penetrationIcon,
  'set.revenge': revengeIcon,
  'set.injury': injuryIcon,
  'set.protection': protectionIcon,
  'set.torrent': torrentIcon,
  'set.reversal': reversalIcon,
  'set.riposte': riposteIcon,
  'set.warfare': warfareIcon,
  'set.pursuit': pursuitIcon,
  'set.weakening': weakeningIcon,
  'set.fervor': fervorIcon,
});

export interface CompletedSetIcon {
  key: string;
  label: string;
  source: string | null;
}

export function completedSetIcons(
  sets: readonly OptimizerResultSetSummary[],
): CompletedSetIcon[] {
  return sets.flatMap((set) => Array.from(
    { length: set.activations },
    (_, activationIndex) => ({
      key: `${set.setId}:${activationIndex}`,
      label: set.label,
      source: FRIBBELS_SET_ICONS[set.setId] ?? null,
    }),
  ));
}
