"""
combat.py — Turn-based combat for Delve.

Each fight is managed by a CombatSession. The session's public interface is:
  player_attack()   — player takes one full round (player hits, NPC retaliates)
  done              — True when the fight is over
  player_won        — True when the NPC was killed

Symmetry
────────
Both sides use the same passive functions (parry, stun, knockback, dodge,
riposte, counter, bleed, etc.), drawn from styles.py. This means a guard
with swordplay might parry your blow and riposte, while a wolf with evasion
might sidestep. NPCs feel alive rather than like stat bags.

NPC passives that skip the NPC's own attack (stun, knockback, haymaker) are
only applied offensively (against the player). NPC-side defensive passives:
  parry, dodge    — reduce or cancel player damage to the NPC
  riposte, counter — deal bonus damage back to the player after parry/dodge

Room flags
──────────
  safe_combat   — player takes zero damage (training rooms)
  reduced_stats — everyone uses half attack/defense (challenge rooms)
  no_combat     — attack command blocked before this code is reached

Kill scripts
────────────
NPCs can carry a kill_script = [...] array of script ops. CombatSession runs
it via ScriptRunner when the NPC dies (and the room is not safe_combat). This
is how the Ashwood Drake drops its fang and advances the quest on death.

Bleed tracking
──────────────
_player_bleed and _npc_bleed are per-session booleans. Bleed ticks once per
round for additional damage; only one bleed source per side at a time.
"""

from __future__ import annotations
import random
from engine.events import EventBus, Event
from engine.player import Player, item_on_hit_effects
from engine.msg import Msg, Tag
from engine.room_flags import RoomFlags
import engine.styles as styles_mod
import engine.log as log


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


# ── Shared damage calculation helpers ────────────────────────────────────────

def _base_damage(atk: int, defense: int) -> int:
    return _clamp(atk - defense + random.randint(-2, 4), 1, 999)


def _npc_style(npc: dict) -> dict | None:
    return styles_mod.get(npc.get("style", ""))


def _npc_prof(npc: dict) -> float:
    return float(npc.get("style_prof", 0))


# ── CombatSession ────────────────────────────────────────────────────────────

def npc_damage(npc: dict, player: "Player") -> int:
    """
    Calculate raw damage for a single opportunistic NPC strike against player.
    Used for free attacks on equip/use. No style bonuses, no special effects —
    just a quick d6 + attack vs player defense roll.
    Returns damage dealt (minimum 0).
    """
    import random
    n_atk = npc.get("attack", 5)
    p_def = player.effective_defense
    roll  = random.randint(1, 6)
    dmg   = max(0, n_atk + roll - p_def - random.randint(0, 3))
    return dmg


class CombatSession:
    def __init__(self, player: Player, npc: dict, bus: EventBus, room: dict, ctx=None):
        self.player = player
        self.npc    = npc
        self.bus    = bus
        self.room   = room
        self.ctx    = ctx   # GameContext — optional, enables kill_script execution
        self.done         = False
        self.player_won   = False
        self._player_bleed = False   # bleed applied to NPC by player
        self._npc_bleed    = False   # bleed applied to player by NPC
        log.debug("combat", "CombatSession created",
                  player=player.name,
                  npc=npc.get("name","?"),
                  npc_id=npc.get("id","?"),
                  npc_hp=npc.get("hp","?"),
                  npc_max_hp=npc.get("max_hp","?"),
                  room=room.get("id","?"),
                  safe=RoomFlags.has(room, RoomFlags.SAFE_COMBAT),
                  spar="spar" in npc.get("tags",[]))

    def _out(self, tag: str, text: str) -> None:
        self.bus.emit(Event.OUTPUT, Msg(tag, text))

    # ── Room flags ────────────────────────────────────────────────────────────

    def _safe(self)    -> bool: return RoomFlags.has(self.room, RoomFlags.SAFE_COMBAT)
    def _spar(self)    -> bool: return "spar" in self.npc.get("tags", [])
    def _reduced(self) -> bool: return RoomFlags.has(self.room, RoomFlags.REDUCED_STATS)

    # ── Stat assembly ─────────────────────────────────────────────────────────

    def _player_stats(self) -> tuple[int, int]:
        """Return (attack, defense) for the player including style and gear bonuses."""
        style = styles_mod.get(self.player.active_style)
        prof  = self.player.style_proficiency()

        p_atk = self.player.effective_attack
        p_def = self.player.effective_defense

        if style:
            # Flat style bonuses
            p_atk += style.get("attack_bonus", 0)
            p_def += int(style.get("defense_bonus", 0))

            # Iron Root iron_skin passive (always-on defense bonus)
            if "iron_skin" in styles_mod.unlocked_passives(style, prof):
                p_def += int(2 + (prof / 100) * 4)

            # Flowing Water stillness passive (always-on defense bonus)
            if "stillness" in styles_mod.unlocked_passives(style, prof):
                p_def += int(1 + (prof / 100) * 5)

            # Gear affinity bonus (scales with proficiency)
            wpn = self.player.equipped.get("weapon")
            arm = self.player.equipped.get("armor")
            atk_mult, def_mult = styles_mod.gear_bonus(style, wpn, arm, prof)
            p_atk = int(p_atk * atk_mult)
            p_def = int(p_def * def_mult)

        if self._reduced():
            p_atk = max(1, p_atk // 2)
            p_def = max(0, p_def // 2)

        # Status effect modifiers
        se = self.player.status_effects
        if "blinded"   in se: p_atk = max(1, p_atk - 4)
        if "weakened"  in se: p_atk = max(1, p_atk - 4)
        if "protected" in se: p_def += 3

        log.debug("combat", "player_stats",
                  base_atk=self.player.effective_attack,
                  base_def=self.player.effective_defense,
                  style=self.player.active_style, style_prof=round(prof,1),
                  final_atk=p_atk, final_def=p_def,
                  status_effects=list(se.keys()) or None)
        return p_atk, p_def

    def _npc_stats(self) -> tuple[int, int]:
        """Return (attack, defense) for the NPC including their style bonuses."""
        style = _npc_style(self.npc)
        prof  = _npc_prof(self.npc)

        n_atk = self.npc.get("attack", 5)
        n_def = self.npc.get("defense", 0)

        if style:
            n_atk += style.get("attack_bonus", 0)
            n_def += int(style.get("defense_bonus", 0))
            if "iron_skin" in styles_mod.unlocked_passives(style, prof):
                n_def += int(2 + (prof / 100) * 4)

        if self._reduced():
            n_atk = max(1, n_atk // 2)
            n_def = max(0, n_def // 2)

        log.debug("combat", "npc_stats",
                  npc=self.npc.get("name","?"),
                  style=self.npc.get("style","none"), style_prof=round(prof,1),
                  final_atk=n_atk, final_def=n_def)
        return n_atk, n_def

    # ── Main round ────────────────────────────────────────────────────────────

    def player_attack(self) -> None:
        if self.done:
            return

        p_style = styles_mod.get(self.player.active_style)
        p_prof  = self.player.style_proficiency()
        n_style = _npc_style(self.npc)
        n_prof  = _npc_prof(self.npc)
        npc_max = self.npc.get("max_hp", 10)

        p_atk, p_def = self._player_stats()
        n_atk, n_def = self._npc_stats()

        log.enter("combat", "player_attack",
                  player=self.player.name, player_hp=self.player.hp,
                  npc=self.npc.get("name","?"), npc_hp=self.npc.get("hp","?"),
                  p_atk=p_atk, p_def=p_def, n_atk=n_atk, n_def=n_def,
                  p_style=self.player.active_style,
                  player_bleed=self._player_bleed, npc_bleed=self._npc_bleed)

        # ── Bleed ticks ───────────────────────────────────────────────────────
        if self._player_bleed:
            bd = random.randint(1, 3)
            self.npc["hp"] -= bd
            log.debug("combat", "bleed: player->npc", dmg=bd, npc_hp_after=self.npc["hp"])
            self._out(Tag.COMBAT_HIT, f"The wound bleeds for {bd} damage.")
            if self.npc["hp"] <= 0:
                self._finish_npc_dead(p_style, p_prof); return

        if self._npc_bleed and not self._safe():
            bd = random.randint(1, 2)
            self.player.hp -= bd
            log.debug("combat", "bleed: npc->player", dmg=bd, player_hp_after=self.player.hp)
            self._out(Tag.COMBAT_RECV, f"Your wound bleeds for {bd} damage. "
                      f"({self.player.hp}/{self.player.max_hp} HP)")
            if not self.player.is_alive:
                self._finish_player_dead(); return

        # ── Companion attack (fires once per round, after bleed, before player) ──
        import engine.companion as companion_mod
        comp = self.player.companion
        if (companion_mod.is_active(comp)
                and companion_mod.companion_type(comp) == "combat"
                and self.npc.get("hp", 0) > 0):
            companion_mod.companion_attack(comp, self.npc, self.bus, self._safe())
            log.debug("combat", "companion attacked", npc_hp_after=self.npc.get("hp","?"))
            if self.npc["hp"] <= 0:
                self._finish_npc_dead(
                    styles_mod.get(self.player.active_style),
                    self.player.style_proficiency()
                )
                return

        # ══════════════════════════════════════════════════════════════════════
        # PLAYER → NPC
        # ══════════════════════════════════════════════════════════════════════

        # Style matchup
        p_mult, p_reason = styles_mod.matchup(p_style, self.npc) if p_style else (1.0, "")

        # Base damage + pre-hit passives
        base = _base_damage(p_atk, n_def)
        log.debug("combat", "player->npc: base damage",
                  p_atk=p_atk, n_def=n_def, base_dmg=base,
                  matchup_mult=round(p_mult,2), matchup_reason=p_reason or "none")

        if p_style:
            up = styles_mod.unlocked_passives(p_style, p_prof)

            if "vital_strike" in up:
                ok, frag = styles_mod.check_passive("vital_strike", p_prof)
                if ok:
                    base *= 2
                    log.debug("combat", "passive: vital_strike fired", base_after=base)
                    self._out(Tag.COMBAT_HIT, f"You {frag}")

            if "haymaker" in up:
                ok, frag = styles_mod.check_passive("haymaker", p_prof)
                if ok:
                    base = int(base * 1.75)
                    log.debug("combat", "passive: haymaker fired", base_after=base)
                    self._out(Tag.COMBAT_HIT, f"You {frag}")

        p_dmg = max(1, int(base * p_mult))
        log.debug("combat", "player->npc: final damage", p_dmg=p_dmg)

        # NPC defensive passives (parry / dodge) — can reduce or cancel hit
        npc_parried = False
        npc_dodged  = False

        if n_style:
            n_up = styles_mod.unlocked_passives(n_style, n_prof)

            if "parry" in n_up:
                ok, frag = styles_mod.check_passive("parry", n_prof)
                if ok:
                    npc_parried = True
                    log.debug("combat", "npc passive: parry fired")
                    self._out(Tag.COMBAT_RECV, f"{self.npc['name']} {frag}")
                    # NPC riposte
                    if "riposte" in n_up:
                        ok2, frag2 = styles_mod.check_passive("riposte", n_prof)
                        if ok2 and not self._safe():
                            rdmg = max(1, int(n_atk * 0.6))
                            self.player.hp -= rdmg
                            log.debug("combat", "npc passive: riposte",
                                      rdmg=rdmg, player_hp_after=self.player.hp)
                            self._out(Tag.COMBAT_RECV,
                                      f"{self.npc['name']} {frag2} {rdmg} damage back! "
                                      f"({self.player.hp}/{self.player.max_hp} HP)")
                            if not self.player.is_alive:
                                self._finish_player_dead(); return

            if not npc_parried and "dodge" in n_up:
                ok, frag = styles_mod.check_passive("dodge", n_prof)
                if ok:
                    npc_dodged = True
                    log.debug("combat", "npc passive: dodge fired")
                    self._out(Tag.COMBAT_RECV, f"{self.npc['name']} {frag}")
                    if "counter" in n_up:
                        ok2, frag2 = styles_mod.check_passive("counter", n_prof)
                        if ok2 and not self._safe():
                            cdmg = max(1, int(n_atk * 0.5))
                            self.player.hp -= cdmg
                            log.debug("combat", "npc passive: counter",
                                      cdmg=cdmg, player_hp_after=self.player.hp)
                            self._out(Tag.COMBAT_RECV,
                                      f"{self.npc['name']} {frag2} {cdmg} damage! "
                                      f"({self.player.hp}/{self.player.max_hp} HP)")
                            if not self.player.is_alive:
                                self._finish_player_dead(); return

        if not npc_parried and not npc_dodged:
            self.npc["hp"] = self.npc.get("hp", npc_max) - p_dmg
            log.debug("combat", "player->npc: hit landed",
                      p_dmg=p_dmg, npc_hp_after=self.npc["hp"])
            msg = f"You strike {self.npc['name']} for {p_dmg} damage."
            if p_reason:
                msg += f" [{p_style['name'] if p_style else ''}: {p_reason}]"
            msg += f" ({max(0, self.npc['hp'])}/{npc_max} HP)"
            self._out(Tag.COMBAT_HIT, msg)

            # Item on_hit effects
            wpn = self.player.equipped.get("weapon")
            if wpn:
                for fx in item_on_hit_effects(wpn):
                    ability = fx.get("ability", "")
                    chance  = float(fx.get("chance", 0))
                    mag     = int(fx.get("magnitude", 1))
                    if random.random() < chance:
                        if ability == "bleed" and not self._player_bleed:
                            self._player_bleed = True
                            log.debug("combat", "item on_hit: bleed applied",
                                      weapon=wpn.get("name","?"))
                            self._out(Tag.COMBAT_HIT,
                                      f"Your {wpn['name']} opens a bleeding wound!")
                        elif ability == "stun":
                            log.debug("combat", "item on_hit: stun - NPC turn skipped",
                                      weapon=wpn.get("name","?"))
                            self._out(Tag.COMBAT_HIT,
                                      f"Your {wpn['name']} stuns {self.npc['name']}!")
                            if self.npc["hp"] > 0:
                                return  # NPC turn skipped

            # Player style bleed passive
            if p_style and "bleed" in styles_mod.unlocked_passives(p_style, p_prof) \
                    and not self._player_bleed:
                ok, frag = styles_mod.check_passive("bleed", p_prof)
                if ok:
                    self._player_bleed = True
                    log.debug("combat", "player passive: bleed applied")
                    self._out(Tag.COMBAT_HIT, f"You {frag}")
        else:
            log.debug("combat", "player->npc: hit blocked",
                      npc_parried=npc_parried, npc_dodged=npc_dodged)

        if self.npc["hp"] <= 0:
            self._finish_npc_dead(p_style, p_prof); return

        # ══════════════════════════════════════════════════════════════════════
        # NPC → PLAYER
        # ══════════════════════════════════════════════════════════════════════

        n_mult, n_reason = (1.0, "")
        n_base = _base_damage(n_atk, p_def)
        log.debug("combat", "npc->player: base damage",
                  n_atk=n_atk, p_def=p_def, n_base=n_base)

        if n_style:
            n_up2 = styles_mod.unlocked_passives(n_style, n_prof)

            # NPC offensive passives
            if "vital_strike" in n_up2:
                ok, frag = styles_mod.check_passive("vital_strike", n_prof)
                if ok:
                    n_base *= 2
                    log.debug("combat", "npc passive: vital_strike fired", n_base_after=n_base)
                    self._out(Tag.COMBAT_RECV, f"{self.npc['name']} {frag}")
            if "haymaker" in n_up2:
                ok, frag = styles_mod.check_passive("haymaker", n_prof)
                if ok:
                    n_base = int(n_base * 1.75)
                    log.debug("combat", "npc passive: haymaker fired", n_base_after=n_base)
                    self._out(Tag.COMBAT_RECV, f"{self.npc['name']} {frag}")

        n_dmg = max(1, int(n_base * n_mult))
        log.debug("combat", "npc->player: final damage", n_dmg=n_dmg)

        # Player defensive passives
        p_dodged  = False
        p_parried = False

        if p_style:
            p_up = styles_mod.unlocked_passives(p_style, p_prof)

            if "stun" in p_up:
                ok, frag = styles_mod.check_passive("stun", p_prof)
                if ok:
                    log.debug("combat", "player passive: stun - NPC turn skipped")
                    self._out(Tag.COMBAT_HIT, f"{self.npc['name']} {frag}")
                    return   # NPC skips their attack

            if "knockback" in p_up:
                ok, frag = styles_mod.check_passive("knockback", p_prof)
                if ok:
                    log.debug("combat", "player passive: knockback - NPC turn skipped")
                    self._out(Tag.COMBAT_HIT, f"You {frag}")
                    return

            if "dodge" in p_up:
                ok, frag = styles_mod.check_passive("dodge", p_prof)
                if ok:
                    p_dodged = True
                    log.debug("combat", "player passive: dodge fired")
                    self._out(Tag.COMBAT_RECV, f"You {frag}")
                    if "counter" in p_up:
                        ok2, frag2 = styles_mod.check_passive("counter", p_prof)
                        if ok2:
                            cdmg = max(1, int(p_atk * 0.5 * p_mult))
                            self.npc["hp"] -= cdmg
                            log.debug("combat", "player passive: counter",
                                      cdmg=cdmg, npc_hp_after=self.npc["hp"])
                            self._out(Tag.COMBAT_HIT,
                                      f"You {frag2} {cdmg} counter damage. "
                                      f"({max(0,self.npc['hp'])}/{npc_max} HP)")
                            if self.npc["hp"] <= 0:
                                self._finish_npc_dead(p_style, p_prof); return

            if not p_dodged and "parry" in p_up:
                ok, frag = styles_mod.check_passive("parry", p_prof)
                if ok:
                    p_parried = True
                    log.debug("combat", "player passive: parry fired")
                    self._out(Tag.COMBAT_RECV, f"You {frag}")
                    if "riposte" in p_up:
                        ok2, frag2 = styles_mod.check_passive("riposte", p_prof)
                        if ok2:
                            rdmg = max(1, int(p_atk * 0.6 * p_mult))
                            self.npc["hp"] -= rdmg
                            log.debug("combat", "player passive: riposte",
                                      rdmg=rdmg, npc_hp_after=self.npc["hp"])
                            self._out(Tag.COMBAT_HIT,
                                      f"You {frag2} {rdmg} riposte damage. "
                                      f"({max(0,self.npc['hp'])}/{npc_max} HP)")
                            if self.npc["hp"] <= 0:
                                self._finish_npc_dead(p_style, p_prof); return

        # Flowing Water redirect — convert enemy momentum into counterattack
        if not p_dodged and not p_parried and p_style:
            p_up = styles_mod.unlocked_passives(p_style, p_prof)
            if "redirect" in p_up:
                ok, frag = styles_mod.check_passive("redirect", p_prof)
                if ok:
                    n_tags = self.npc.get("tags", [])
                    speed_bonus = 1.4 if "fast" in n_tags else (0.9 if "slow" in n_tags else 1.0)
                    rdmg = max(1, int(p_atk * 0.7 * p_mult * speed_bonus))
                    self.npc["hp"] -= rdmg
                    log.debug("combat", "player passive: redirect",
                              speed_bonus=speed_bonus, rdmg=rdmg,
                              npc_hp_after=self.npc["hp"])
                    self._out(Tag.COMBAT_HIT, f"You {frag} ({rdmg} damage). "
                              f"({max(0,self.npc['hp'])}/{npc_max} HP)")
                    p_dodged = True   # also absorb the incoming hit
                    if "absorb" in p_up:
                        ok2, frag2 = styles_mod.check_passive("absorb", p_prof)
                        if ok2:
                            heal = max(1, rdmg // 3)
                            self.player.hp = min(self.player.max_hp, self.player.hp + heal)
                            log.debug("combat", "player passive: absorb",
                                      heal=heal, player_hp_after=self.player.hp)
                            self._out(Tag.COMBAT_HIT,
                                      f"You {frag2} (+{heal} HP).")
                    if self.npc["hp"] <= 0:
                        self._finish_npc_dead(p_style, p_prof); return

        # NPC on-hit bleed
        if n_style and "bleed" in styles_mod.unlocked_passives(n_style, n_prof) \
                and not self._npc_bleed:
            ok, frag = styles_mod.check_passive("bleed", n_prof)
            if ok:
                self._npc_bleed = True
                log.debug("combat", "npc passive: bleed applied to player")

        if not p_dodged and not p_parried:
            if self._safe():
                self._out(Tag.COMBAT_RECV,
                          f"{self.npc['name']} strikes at you for {n_dmg} — "
                          f"but you are unharmed. [safe zone]")
            else:
                self.player.hp -= n_dmg
                log.debug("combat", "npc->player: hit landed",
                          n_dmg=n_dmg, player_hp_after=self.player.hp)
                msg = f"{self.npc['name']} hits you for {n_dmg} damage."
                if n_reason:
                    msg += f" [{n_style['name'] if n_style else ''}: {n_reason}]"
                msg += f" ({self.player.hp}/{self.player.max_hp} HP)"
                self._out(Tag.COMBAT_RECV, msg)
                if self._npc_bleed:
                    self._out(Tag.COMBAT_RECV,
                              f"{self.npc['name']}'s strike leaves you bleeding!")
                if not self.player.is_alive:
                    self._finish_player_dead()
        else:
            log.debug("combat", "npc->player: incoming blocked",
                      p_dodged=p_dodged, p_parried=p_parried)

        # ── NPC may also strike the companion (30% chance per round) ──────────
        import engine.companion as companion_mod
        comp = self.player.companion
        if (not self._safe()
                and companion_mod.is_active(comp)
                and companion_mod.companion_type(comp) == "combat"
                and random.random() < 0.30):
            companion_mod.companion_take_hit(comp, self.npc, self.bus)

    # ── Outcomes ─────────────────────────────────────────────────────────────

    def _finish_npc_dead(self, style: dict | None, prof: float) -> None:
        log.info("combat", "_finish_npc_dead",
                 npc=self.npc.get("name","?"), npc_id=self.npc.get("id","?"),
                 spar=self._spar(), player_hp=self.player.hp,
                 player_xp=self.player.xp, player_gold=self.player.gold)

        # ── Spar floor: NPCs with "spar" tag yield at 1 HP, not die ──────────
        if self._spar():
            self.npc["hp"] = 1
            xp = max(1, self.npc.get("xp_reward", 0) // 2)
            self.player.gain_xp(xp)
            log.info("combat", "spar yield: NPC goes non-hostile",
                     xp_awarded=xp, kill_script=bool(self.npc.get("kill_script")))
            self._out(Tag.COMBAT_KILL,
                      f"{self.npc['name']} drops their guard, breathing hard. "
                      f"Yield accepted.")
            if xp:
                self._out(Tag.REWARD_XP, f"  You earn {xp} XP.")
            kill_script = self.npc.get("kill_script", [])
            if kill_script and self.ctx:
                from engine.script import ScriptRunner
                ScriptRunner(self.ctx).run(kill_script)
            self.done        = True
            self.player_won  = True
            self.npc["hostile"] = False
            return

        xp      = self.npc.get("xp_reward", 10)
        gold    = self.npc.get("gold_reward", 0)
        leveled = self.player.gain_xp(xp)
        self.player.gold += gold
        log.info("combat", "NPC killed",
                 xp_reward=xp, gold_reward=gold, leveled=leveled,
                 player_xp_after=self.player.xp, player_level=self.player.level,
                 safe_room=self._safe())

        self._out(Tag.COMBAT_KILL, f"{self.npc['name']} is defeated!")
        if xp:
            if self.player.xp_debt > 0:
                self._out(Tag.REWARD_XP,
                          f"  XP applied to debt. ({self.player.xp_debt} remaining)")
            else:
                self._out(Tag.REWARD_XP, f"  You gain {xp} XP.")
        if gold: self._out(Tag.REWARD_GOLD,  f"  You find {gold} gold.")
        if leveled:
            self._out(Tag.REWARD_XP,
                      f"✦ Level up! You are now level {self.player.level}. HP fully restored.")

        # Style proficiency
        if style:
            old_p = self.player.style_proficiency()
            gain  = styles_mod.proficiency_gain(
                style, self.npc, old_p, is_training=self._safe()
            )
            if gain > 0:
                new_p = styles_mod.apply_gain(
                    self.player.style_prof, self.player.active_style, gain
                )
                # Only tell the player when the visible (integer) value changes
                if int(new_p) > int(old_p):
                    self._out(Tag.REWARD_XP,
                              f"  {style['name']} proficiency: {old_p:.0f} → {new_p:.0f}/100")
                for ab in styles_mod.newly_unlocked(style, old_p, new_p):
                    self._out(Tag.REWARD_XP,
                              f"  ✦ New ability unlocked: {ab.replace('_',' ').title()}!")

        # Run NPC kill_script if present (and not in safe/training room)
        kill_script = self.npc.get("kill_script", [])
        if kill_script and not self._safe() and self.ctx:
            from engine.script import ScriptRunner
            ScriptRunner(self.ctx).run(kill_script)

        if self._safe():
            self.npc["hp"]       = self.npc.get("max_hp", 10)
            self._player_bleed   = False
            self._npc_bleed      = False
            self._out(Tag.SYSTEM,
                      f"{self.npc['name']} shakes it off and readies itself again. [training respawn]")
            self.done = False
        else:
            self.done       = True
            self.player_won = True

    def _finish_player_dead(self) -> None:
        """
        Handle player death:
          1. Emit narrative death message.
          2. Drop a timed corpse (all non-no_drop items) in the death room.
          3. Strip inventory and equipped items (no_drop items stay).
          4. Lose all carried gold.
          5. Set xp_debt to 25% of current XP (blocks XP gain until paid off).
          6. Restore HP and move player to their bind_room (or start_room).
          7. Emit PLAYER_DIED so the frontend can display the respawn notice.
        """
        from copy import deepcopy

        p = self.player
        world = self.ctx.world if self.ctx else None

        log.info("combat", "PLAYER DIED",
                 npc=self.npc.get("name","?"), room=p.room_id,
                 player_hp=p.hp, player_xp=p.xp, player_gold=p.gold,
                 inventory_count=len(p.inventory),
                 xp_debt_will_be=max(0, p.xp // 4),
                 bind_room=p.bind_room or "(none)")

        # ── 1. Narrative ───────────────────────────────────────────────────────
        self._out(Tag.COMBAT_DEATH,
                  "You have been slain. The world goes dark.")

        # ── 2 & 3. Build corpse, strip inventory/equipped ──────────────────────
        no_drop_inv  = [i for i in p.inventory if i.get("no_drop")]
        drop_inv     = [deepcopy(i) for i in p.inventory if not i.get("no_drop")]

        no_drop_equip: dict = {}
        drop_equip:    dict = {}
        for slot, item in p.equipped.items():
            if item is None:
                no_drop_equip[slot] = None
            elif item.get("no_drop"):
                no_drop_equip[slot] = item
            else:
                drop_equip[slot] = deepcopy(item)
                no_drop_equip[slot] = None

        if world:
            world.drop_corpse(p.room_id, p.name, drop_inv, drop_equip)

        p.inventory = no_drop_inv
        p.equipped  = {slot: no_drop_equip.get(slot)
                       for slot in ("weapon", "armor", "pack")}

        # ── 4. Lose carried gold ───────────────────────────────────────────────
        p.gold = 0

        # ── 5. XP debt (25% of current XP, minimum 0) ─────────────────────────
        p.xp_debt = max(0, p.xp // 4)

        # ── 6. Respawn ─────────────────────────────────────────────────────────
        respawn = p.bind_room or (world.start_room if world else "")
        if respawn:
            p.room_id = respawn
        p.hp = p.max_hp

        log.info("combat", "player respawned",
                 room=p.room_id, hp=p.hp, xp_debt=p.xp_debt,
                 inventory_kept=[i.get("id","?") for i in p.inventory])

        self.done       = True
        self.player_won = False

        # ── 7. Signal frontend ─────────────────────────────────────────────────
        self.bus.emit(Event.PLAYER_DIED)
