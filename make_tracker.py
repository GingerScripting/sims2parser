#!/usr/bin/env python3
"""Generate RebeccaAchievementTracker.package — v5, neighborhood-wide storage.

Architecture (see memory: manage-inventory-format):
- Done-flags live in the NEIGHBORHOOD GLOBAL inventory as one token GUID per
  item (GUID_BASE + id); token present = done. Uses ONLY the operations the
  in-game diagnostic proved on the global inventory: add / remove / find /
  count. No bind, no token properties (bind fails on the global inventory).
- Every GUID ships with a hidden type-4 OBJD (ACR token style: no TTAB
  reference, no catalog sort flags), all in one group.
- Three wall plaques (cloned from Christianlov's diploma, Maxis mesh) are the
  UI: Achievements (63) + Import/Test interactions, Lifetime Wants (36),
  Custom LTWs (50). Toggling shows a side-screen notification.
- No token code ever runs at lot load (init is inert); toggles are
  straight-line (no loops); the test tree's cleanup loop is iteration-capped.

One package, four groups: 3 plaques + 1 token-definition group.

Usage: python3 make_tracker.py
"""

import csv
import struct
from pathlib import Path

import s2parser
from s2object import (
    RET_TRUE, RET_ERROR,
    OP_DIALOG, OP_INVENTORY,
    EXPR_EQ, EXPR_ADD, EXPR_SET,
    OWNER_LITERAL, OWNER_LOCAL,
    INV_ADD, INV_REMOVE, INV_FIND, INV_BIND, INV_GETPROP, INV_SETPROP,
    INV_COMMIT, INV_COUNT, EXPR_GT,
    TYPE_BHAV, TYPE_CTSS, TYPE_GLOB, TYPE_NREF, TYPE_OBJD, TYPE_OBJF,
    TYPE_SLOT, TYPE_STR, TYPE_TTAB, TYPE_TTAS,
    Asm, instr, expr, expr_ops, dialog_ops, inv_ops, bhav, str_resource, ttab,
    ttab_entry_template, patch_objd,
)
from s2writer import Resource, write_package, read_all_resources

PROJECT = Path(__file__).parent
PLAQUE_DONOR = PROJECT / 'sample-packages' / 'Christianlov_CounterfeitCollegeDiploma.package'
TOKEN_DONOR = PROJECT / 'LTW_4UniCareers.package'
TOKEN_DONOR_GROUP = 0x7F84F7C6  # Memory_Career_Top_Artist group
CSV_DIR = Path.home() / 'Documents' / 'The Sims 2'
OUTPUT = PROJECT / 'RebeccaAchievementTracker.package'

# One token GUID per item: GUID_BASE + item id. Presence in the global
# inventory = done. Every GUID ships with a hidden OBJD (ACR token style).
GUID_BASE = 0xB3CCA800
SENTINEL_ID = 200  # marks "CSV pre-seed done" (its own token GUID)
TEST_ID = 199      # scratch GUID for the Test Token Store diagnostic

# Diagnostics build: toggles/import disabled; Test Token Store becomes a
# category/GUID-filter fact-chain (populated hoods showed find() matching
# foreign tokens when the category byte is 0).
DIAG_MODE = True
CAT_A, CAT_B, CAT_PROBE = 42, 43, 77

PLAQUES = [  # (key, group, guid, catalog name)
    ('ach', 0x7FB3C001, 0xB3CCA701, 'Achievement Tracker'),
    ('ltw', 0x7FB3C002, 0xB3CCA703, 'Lifetime Wants Tracker'),
    ('custom', 0x7FB3C003, 0xB3CCA704, 'Custom Wants Tracker'),
]
TOKEN_GROUP = 0x7FB3C004

T_INIT, T_MAIN, T_VIEW, T_ABOUT, T_GUARD = 0x1000, 0x1001, 0x1002, 0x1003, 0x1004
T_IMPORT, T_TEST = 0x1005, 0x1006
T_TOGGLE_BASE = 0x1010

# --------------------------------------------------------------------------
# Item data (categories keyed by exact CSV text; ids are 1-based, global)
# --------------------------------------------------------------------------

ACH_CATEGORY = {
    'Make a plantsim': 'Supernatural',
    'Make a traveler sim that has all the vacation achievements': 'Misc',
    'Run a successful beauty parlor': 'Business',
    'Business managed entirely by phone': 'Business',
    'Make a zombie': 'Supernatural',
    'Marry a zombie': 'Supernatural',
    'Make a servo': 'Supernatural',
    'Marry a servo': 'Supernatural',
    'Make a vampire family': 'Supernatural',
    'Make a werewolf': 'Supernatural',
    'Marry a werewolf': 'Supernatural',
    'Fulfill every lifetime wish': 'Lifetime Wishes',
    'Get 1,000,000 simoleons': 'Misc',
    'Max out all skills': 'Skills & Talents',
    'Get perma-platinum with a Family sim': 'Perma-Platinum',
    'Get perma-platinum with a Fortune sim': 'Perma-Platinum',
    'Get perma-platinum with a Knowledge sim': 'Perma-Platinum',
    'Get perma-platinum with a Popularity sim': 'Perma-Platinum',
    'Get perma-platinum with a Pleasure sim': 'Perma-Platinum',
    'Get perma-platinum with a Grilled Cheese sim': 'Perma-Platinum',
    'Rank 10 business': 'Business',
    'Death by disease': 'Deaths',
    'Death by drowning': 'Deaths',
    'Death by electrocution': 'Deaths',
    'Death by fire': 'Deaths',
    'Death by flies': 'Deaths',
    'Death by fright': 'Deaths',
    'Death by old age': 'Deaths',
    'Death by satellite': 'Deaths',
    'Death by starvation': 'Deaths',
    'Death by hail': 'Deaths',
    'Join the Garden Club': 'Misc',
    'Find Bigfoot': 'Supernatural',
    'Level 6 Greek House': 'College',
    'Join secret society': 'College',
    'Have a sim drop out of college': 'College',
    'Have a sim get kicked out of college from academic probation': 'College',
    'Have a litter of cats': 'Pets',
    'Have a litter of dogs': 'Pets',
    'Have a big man/woman on campus': 'College',
    'Have a maxed-out personality': 'Skills & Talents',
    'Mount 20 fish on the wall': 'Skills & Talents',
    'Make a gardener sim': 'Skills & Talents',
    'Have a terrible date': 'Misc',
    'Have a terrible vacation': 'Misc',
    'Romance two social bunnies': 'Misc',
    'Do the asylum challenge': 'Misc',
    'Have a successful bakery': 'Business',
    'Have a successful flower shop': 'Business',
    'Have a sim that is a professional painter': 'Skills & Talents',
    'Have a sim that is a professional gardener': 'Skills & Talents',
    'Play a family for five generations': 'Misc',
    'Get a sim really good at meditation': 'Skills & Talents',
    'Get a sim really good at yoga': 'Skills & Talents',
    'Get the alien abduction scholarship': 'College',
    'Have a novelist sim': 'Skills & Talents',
    'Make a custom dog or cat breed': 'Pets',
    'Make a crazy cat lady (1 woman, 7 cats)': 'Pets',
    'Make a sim family that lives off the land (no groceries)': 'Misc',
    'Achieve a lifetime wish': 'Lifetime Wishes',
    'Achieve a lifetime wish as an elder': 'Lifetime Wishes',
    'Achieve a lifetime wish as an adult': 'Lifetime Wishes',
    'Achieve a lifetime wish as a teen': 'Lifetime Wishes',
}

LTW_CATEGORY = {
    'Become Celebrity Chef': 'Careers (Base)',
    'Become Professional Party Guest': 'Careers (Base)',
    'Become General': 'Careers (Base)',
    'Become Hall of Famer': 'Careers (Base)',
    'Become Chief of Staff': 'Careers (Base)',
    'Become Mad Scientist': 'Careers (Base)',
    'Become Mayor': 'Careers (Base)',
    'Become Captain Hero': 'Careers (Base)',
    'Become Criminal Mastermind': 'Careers (Base)',
    'Become Business Tycoon': 'Careers (Base)',
    'Become Space Pirate': 'Careers (EP & Uni)',
    'Become Education Minister': 'Careers (EP & Uni)',
    'Become Game Designer': 'Careers (EP & Uni)',
    'Become Media Magnate': 'Careers (EP & Uni)',
    'Become The Law': 'Careers (EP & Uni)',
    'Become Rock God': 'Careers (EP & Uni)',
    'Graduate 3 Children from College': 'Family',
    'Have 6 Grandchildren': 'Family',
    'Marry off 6 Children': 'Family',
    'Reach Golden Anniversary': 'Family',
    'Have 20 Simultaneous Best Friends': 'Romance & Friends',
    'Have 20 Simultaneous Lovers': 'Romance & Friends',
    'WooHoo with 20 Different Sims': 'Romance & Friends',
    'Have 50 Dream Dates': 'Romance & Friends',
    'Have 50 1st Dates': 'Romance & Friends',
    'Earn §100,000': 'Wealth & Skills',
    'Max out 7 Skills': 'Wealth & Skills',
    'Own 5 Top Level Businesses': 'Wealth & Skills',
    'Eat 200 Grilled Cheese Sandwiches': 'Wealth & Skills',
    'Raise 20 Puppies or Kittens': 'Pets',
    'Have 20 Simultaneous Pet Best Friends': 'Pets',
    'Have 6 Pets Reach the Top Career Level': 'Pets',
}

UNI_LTWS = ['Become Visionary', 'Become Icon', 'Become Cult Leader',
            'Become Ecological Guru']

LAMARE_LTWS = {
    'Crafts': ['Sew 15 Items', 'Sculpt 15 Pottery Pieces', 'Make 15 Toys',
               'Create 15 Robotic Devices', 'Make 20 Flower Arrangements',
               'Restore 3 Cars', 'Perform 30 Salon Makeovers',
               'Publish Bestselling Novel', 'Paint Masterpiece',
               'Gain 6 Gold Badges'],
    'Hobbies': ['Collect All Vacation Mementos', 'Have 5 Good Vacations',
                'Achieve Ultimate Wellness', 'Become Astronomer',
                'Catch 50 Fish', 'Complete Insect Collection',
                'Max 3 Hobby Enthusiasms', 'Harvest 75 Best Produce',
                'Repair 40 Objects', 'Become Insider Member'],
    'Money': ['Earn by Street Performing', 'Earn as Personal Trainer',
              'Become Drinks Master', 'Make Money at Poker',
              'Make Money Hustling Pool', 'Earn as DJ',
              'Earn Playing Instruments', 'Black Widow', 'Perfect Mansion',
              'Self-Employed Talent'],
    'Supernatural': ['Turn Sims Into Vampires', 'Resurrect Sims as Zombies',
                     'Be 3 Supernaturals at Once', 'Surrounded by Robots',
                     'Get Abducted by Aliens', 'Witch Spellcaster',
                     'Witch Potion Seller'],
    'Social': ['Raise a Wonder Child', 'Have 10 Successful Parties',
               'Spend 40 Hours Teaching', 'Maximize and Use Influence',
               'Enemies and Bad Reputation', 'Win Fights with 15 Sims'],
    'Career & Contests': ['Job Hopper', 'Careerist Clan', 'Acclaimed Cook',
                          'Cybersport Gamer', 'Great Dancer', 'Food Critic',
                          'Reach Max Lifetime Aspiration'],
}


def load_items():
    """Return {plaque_key: [(item_id, category, name, done), ...]} with
    globally unique 1-based ids in stable order."""
    def read_csv(path):
        rows = []
        with open(path, newline='', encoding='utf-8-sig') as f:
            for row in csv.reader(f):
                if row and row[0] in ('TRUE', 'FALSE'):
                    rows.append((row[1].strip(), row[0] == 'TRUE'))
        return rows

    plaques = {'ach': [], 'ltw': [], 'custom': []}
    next_id = 1

    seen = set()
    for name, done in read_csv(CSV_DIR / 'Achievement Tracker-Table 1.csv'):
        if name in seen:
            continue  # CSV has a duplicate 'Death by disease' row
        seen.add(name)
        plaques['ach'].append((next_id, ACH_CATEGORY[name], name, done))
        next_id += 1

    for name, done in read_csv(CSV_DIR / 'Lifetime Wants-Table 1.csv'):
        plaques['ltw'].append((next_id, LTW_CATEGORY[name], name, done))
        next_id += 1
    for name in UNI_LTWS:
        plaques['ltw'].append((next_id, 'Careers (EP & Uni)', name, False))
        next_id += 1

    for cat, names in LAMARE_LTWS.items():
        for name in names:
            plaques['custom'].append((next_id, cat, name, False))
            next_id += 1

    assert next_id - 1 < SENTINEL_ID, 'item ids must stay below the sentinel'
    return plaques


# --------------------------------------------------------------------------
# BHAV builders — Mode B: one token GUID per item, presence = done.
# Only operations PROVEN on the global inventory by the in-game diagnostic
# are used: add (00), remove (01), find (03), count (0x0A). No bind, no
# properties (bind failed in-game on the global inventory; ACR's real
# hood-wide writes avoid it too).
# --------------------------------------------------------------------------
# Register use: local0 = find iterator, local1 = count scratch,
# local2 = view counter, local3 = loop guard (test cleanup only).

LOOP_CAP = 250

expr_operands = expr_ops


def item_guid(item_id: int) -> int:
    return GUID_BASE + item_id


def toggle_tree(name, item_id, done_str, notdone_str):
    """find token -> remove + notify off; missing -> add + notify on."""
    g = item_guid(item_id)
    a = Asm()
    a.ins(0x0002, 'find', RET_ERROR,
          expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    a.label('find')
    a.ins(OP_INVENTORY, 'remove', 'add',
          inv_ops(INV_FIND, g, sel_scope=OWNER_LOCAL, sel_id=0))
    a.label('remove')
    a.ins(OP_INVENTORY, 'notify_off', RET_ERROR,
          inv_ops(INV_REMOVE, g, sel_scope=OWNER_LOCAL, sel_id=0))
    a.label('notify_off')
    a.ins(OP_DIALOG, RET_TRUE, RET_TRUE, dialog_ops(notdone_str))
    a.label('add')
    a.ins(OP_INVENTORY, 'notify_on', RET_ERROR, inv_ops(INV_ADD, g))
    a.label('notify_on')
    a.ins(OP_DIALOG, RET_TRUE, RET_TRUE, dialog_ops(done_str))
    return bhav(name, a.assemble(), argc=1, localc=1)


def view_tree(dialog_idx, all_item_ids):
    """local2 = number of done items across ALL trackers (straight-line:
    one find per item, no loops)."""
    a = Asm()
    a.ins(0x0002, 'i0', RET_ERROR,
          expr_operands(2, 0, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    for n, item_id in enumerate(all_item_ids):
        a.label(f'i{n}')
        a.ins(0x0002, f'f{n}', RET_ERROR,
              expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
        a.label(f'f{n}')
        a.ins(OP_INVENTORY, f'c{n}', f'i{n + 1}',
              inv_ops(INV_FIND, item_guid(item_id),
                      sel_scope=OWNER_LOCAL, sel_id=0))
        a.label(f'c{n}')
        a.ins(0x0002, f'i{n + 1}', RET_ERROR,
              expr_operands(2, 1, EXPR_ADD, OWNER_LOCAL, OWNER_LITERAL))
    a.label(f'i{len(all_item_ids)}')
    a.ins(OP_DIALOG, RET_TRUE, RET_TRUE, dialog_ops(dialog_idx))
    return bhav('Interaction - View Progress', a.assemble(), argc=1, localc=3)


def init_tree(donor_init_instr):
    """Inert: painting init only. Token code never runs at lot load."""
    first = bytearray(donor_init_instr)
    struct.pack_into('<H', first, 2, RET_TRUE)
    return bhav('Function - Init', [bytes(first)])


def import_tree(seed_ids, done_idx, already_idx):
    """User-triggered CSV seeding, guarded by the sentinel token. Straight
    line: for each seed id, add its token unless already present."""
    a = Asm()
    a.ins(0x0002, 'sent_find', RET_ERROR,
          expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    a.label('sent_find')
    a.ins(OP_INVENTORY, 'already', 's0',
          inv_ops(INV_FIND, item_guid(SENTINEL_ID),
                  sel_scope=OWNER_LOCAL, sel_id=0))
    for n, item_id in enumerate(seed_ids):
        a.label(f's{n}')
        a.ins(0x0002, f'sf{n}', RET_ERROR,
              expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
        a.label(f'sf{n}')
        a.ins(OP_INVENTORY, f's{n + 1}', f'sa{n}',
              inv_ops(INV_FIND, item_guid(item_id),
                      sel_scope=OWNER_LOCAL, sel_id=0))
        a.label(f'sa{n}')
        a.ins(OP_INVENTORY, f's{n + 1}', RET_ERROR,
              inv_ops(INV_ADD, item_guid(item_id)))
    a.label(f's{len(seed_ids)}')
    a.ins(OP_INVENTORY, 'done', RET_ERROR,
          inv_ops(INV_ADD, item_guid(SENTINEL_ID)))
    a.label('done')
    a.ins(OP_DIALOG, RET_TRUE, RET_TRUE, dialog_ops(done_idx))
    a.label('already')
    a.ins(OP_DIALOG, RET_TRUE, RET_TRUE, dialog_ops(already_idx))
    return bhav('Interaction - Import Spreadsheet', a.assemble(),
                argc=1, localc=1)


DIAG_FACTS = {
    'f1y': '1: wildcard find before add: FOUND something.',
    'f1n': '1: wildcard find before add: nothing.',
    'f2y': '2: bogus-GUID find: FOUND (GUID filter is OFF).',
    'f2n': '2: bogus-GUID find: nothing (GUID filter works).',
    'f3y': '3: category-42 find before add: FOUND (category filter OFF).',
    'f3n': '3: category-42 find before add: nothing (category filter works).',
    'f4y': '4: category-42 add: OK.',
    'f4n': '4: category-42 add FAILED.',
    'f5y': '5: category-42 find after add: FOUND.',
    'f5n': '5: category-42 find after add: NOT FOUND.',
    'f6y': '6: category-43 find: FOUND (category does not isolate).',
    'f6n': '6: category-43 find: nothing (category isolates).',
    'f7ok': '7: category-42 remove: OK.',
    'f7fail': '7: category-42 remove FAILED.',
    'f7skip': '7: remove skipped for safety (category filter looked broken).',
    'f7none': '7: nothing found to remove (unexpected).',
    'f8y': '8: persistence probe planted. SAVE THE LOT now, then quit.',
    'f8n': '8: persistence probe add FAILED.',
    'stub': 'Diagnostics build: toggles and import are disabled. '
            'Run Test Token Store, note the numbered messages, save the lot.',
}


def diag_tree(strs):
    """Fact-chain: each numbered fact shows a notification and continues.
    Read-only finds first; the only remove is gated on the category filter
    having looked intact (fact 3), so no foreign tokens can be eaten."""
    tg = item_guid(TEST_ID)
    a = Asm()

    def seek(label, nxt_t, nxt_f, guid, cat):
        a.label(label)
        a.ins(0x0002, f'{label}.q', RET_ERROR,
              expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
        a.label(f'{label}.q')
        a.ins(OP_INVENTORY, nxt_t, nxt_f,
              inv_ops(INV_FIND, guid, sel_scope=OWNER_LOCAL, sel_id=0, cat=cat))

    def say(label, text_key, nxt):
        a.label(label)
        a.ins(OP_DIALOG, nxt, nxt, dialog_ops(strs[text_key]))

    seek('f1', 'f1y', 'f1n', tg, 0)
    say('f1y', 'f1y', 'f2')
    say('f1n', 'f1n', 'f2')
    seek('f2', 'f2y', 'f2n', UNREG_GUID, 0)
    say('f2y', 'f2y', 'f3')
    say('f2n', 'f2n', 'f3')
    seek('f3', 'f3y_flag', 'f3n_flag', tg, CAT_A)
    a.label('f3y_flag')
    a.ins(0x0002, 'f3y', RET_ERROR,
          expr_operands(2, 1, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    say('f3y', 'f3y', 'f4')
    a.label('f3n_flag')
    a.ins(0x0002, 'f3n', RET_ERROR,
          expr_operands(2, 0, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    say('f3n', 'f3n', 'f4')
    a.label('f4')
    a.ins(OP_INVENTORY, 'f4y', 'f4n', inv_ops(INV_ADD, tg, cat=CAT_A))
    say('f4y', 'f4y', 'f5')
    say('f4n', 'f4n', 'f8')      # cannot continue category tests
    seek('f5', 'f5y', 'f5n', tg, CAT_A)
    say('f5y', 'f5y', 'f6')
    say('f5n', 'f5n', 'f6')
    seek('f6', 'f6y', 'f6n', tg, CAT_B)
    say('f6y', 'f6y', 'f7gate')
    say('f6n', 'f6n', 'f7gate')
    a.label('f7gate')
    a.ins(0x0002, 'f7skip', 'f7', expr_operands(2, 1, EXPR_EQ,
                                                OWNER_LOCAL, OWNER_LITERAL))
    say('f7skip', 'f7skip', 'f8')
    seek('f7', 'f7rm', 'f7none', tg, CAT_A)
    a.label('f7rm')
    a.ins(OP_INVENTORY, 'f7ok', 'f7fail',
          inv_ops(INV_REMOVE, tg, sel_scope=OWNER_LOCAL, sel_id=0, cat=CAT_A))
    say('f7ok', 'f7ok', 'f8')
    say('f7fail', 'f7fail', 'f8')
    say('f7none', 'f7none', 'f8')
    a.label('f8')
    a.ins(OP_INVENTORY, 'f8y', 'f8n',
          inv_ops(INV_ADD, item_guid(TEST_ID), cat=CAT_PROBE))
    say('f8y', 'f8y', RET_TRUE)
    say('f8n', 'f8n', RET_TRUE)
    return bhav('Interaction - Test Token Store', a.assemble(),
                argc=1, localc=3)


def stub_tree(name, stub_idx):
    return bhav(name, [instr(OP_DIALOG, RET_TRUE, RET_TRUE,
                             dialog_ops(stub_idx))], argc=1)


TEST_STAGES = {
    'pass-full': 'Token test: ALL CORE OPS PASS, and unregistered GUIDs '
                 'work too. Best case.',
    'pass-objd': 'Token test: ALL CORE OPS PASS. (Unregistered GUIDs are '
                 'rejected, which is fine - all tracker GUIDs ship with '
                 'object definitions.)',
    'unreg-ghost': 'Token test: core ops pass, but an unregistered token '
                   'added without being findable. Harmless.',
    'unreg-stuck': 'Token test: core ops pass, but the unregistered test '
                   'token could not be removed. Harmless leftover.',
    'add': 'Token test FAILED: add-token returned false.',
    'count-op': 'Token test FAILED: count operation returned false.',
    'count-zero': 'Token test FAILED: add reported success but count is '
                  'still zero.',
    'find': 'Token test FAILED: could not find the token just added.',
    'remove': 'Token test FAILED: remove-token returned false.',
    'gone': 'Token test FAILED: token still findable after remove.',
}

UNREG_GUID = 0xB3CCA7FE  # deliberately shipped WITHOUT an OBJD


def test_tree(stage_strs, guardfail_str):
    """Exercise every operation the tracker relies on, in dependency order,
    plus one informational stage (unregistered GUIDs)."""
    tg = item_guid(TEST_ID)
    a = Asm()

    def fail(stage):
        return f'fail_{stage}'

    # cleanup: remove any stray test tokens from earlier runs (bounded)
    a.ins(0x0002, 'clean_seed', RET_ERROR,
          expr_operands(3, 0, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    a.label('clean_seed')
    a.ins(0x0002, 'clean_find', RET_ERROR,
          expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    a.label('clean_find')
    a.ins(OP_INVENTORY, 'clean_rm', 'add',
          inv_ops(INV_FIND, tg, sel_scope=OWNER_LOCAL, sel_id=0))
    a.label('clean_rm')
    a.ins(OP_INVENTORY, 'clean_step', fail('remove'),
          inv_ops(INV_REMOVE, tg, sel_scope=OWNER_LOCAL, sel_id=0))
    a.label('clean_step')
    a.ins(0x0002, 'clean_cap', RET_ERROR,
          expr_operands(3, 1, EXPR_ADD, OWNER_LOCAL, OWNER_LITERAL))
    a.label('clean_cap')
    a.ins(0x0002, 'guardfail', 'clean_seed',
          expr_operands(3, LOOP_CAP, EXPR_EQ, OWNER_LOCAL, OWNER_LITERAL))
    # add
    a.label('add')
    a.ins(OP_INVENTORY, 'count', fail('add'), inv_ops(INV_ADD, tg))
    # count > 0 proves the add really added
    a.label('count')
    a.ins(OP_INVENTORY, 'count_cmp', fail('count-op'),
          inv_ops(INV_COUNT, tg, val_scope=OWNER_LOCAL, val_id=1))
    a.label('count_cmp')
    a.ins(0x0002, 'find_seed', fail('count-zero'),
          expr_operands(1, 0, EXPR_GT, OWNER_LOCAL, OWNER_LITERAL))
    # find it
    a.label('find_seed')
    a.ins(0x0002, 'find', RET_ERROR,
          expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    a.label('find')
    a.ins(OP_INVENTORY, 'rm', fail('find'),
          inv_ops(INV_FIND, tg, sel_scope=OWNER_LOCAL, sel_id=0))
    # remove it
    a.label('rm')
    a.ins(OP_INVENTORY, 'gone_seed', fail('remove'),
          inv_ops(INV_REMOVE, tg, sel_scope=OWNER_LOCAL, sel_id=0))
    # must be gone now
    a.label('gone_seed')
    a.ins(0x0002, 'gone', RET_ERROR,
          expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    a.label('gone')
    a.ins(OP_INVENTORY, fail('gone'), 'unreg',
          inv_ops(INV_FIND, tg, sel_scope=OWNER_LOCAL, sel_id=0))
    # informational: do unregistered GUIDs work?
    a.label('unreg')
    a.ins(OP_INVENTORY, 'unreg_seed', fail('pass-objd'),
          inv_ops(INV_ADD, UNREG_GUID))
    a.label('unreg_seed')
    a.ins(0x0002, 'unreg_find', RET_ERROR,
          expr_operands(0, 0xFFFF, EXPR_SET, OWNER_LOCAL, OWNER_LITERAL))
    a.label('unreg_find')
    a.ins(OP_INVENTORY, 'unreg_rm', fail('unreg-ghost'),
          inv_ops(INV_FIND, UNREG_GUID, sel_scope=OWNER_LOCAL, sel_id=0))
    a.label('unreg_rm')
    a.ins(OP_INVENTORY, fail('pass-full'), fail('unreg-stuck'),
          inv_ops(INV_REMOVE, UNREG_GUID, sel_scope=OWNER_LOCAL, sel_id=0))
    for stage in TEST_STAGES:
        a.label(fail(stage))
        a.ins(OP_DIALOG, RET_TRUE, RET_TRUE, dialog_ops(stage_strs[stage]))
    a.label('guardfail')
    a.ins(OP_DIALOG, RET_TRUE, RET_TRUE, dialog_ops(guardfail_str))
    return bhav('Interaction - Test Token Store', a.assemble(),
                argc=1, localc=4)


# --------------------------------------------------------------------------

def build_plaque(resources, donor, key, group, guid, cat_name, items,
                 all_item_ids, seed_ids=None):
    def add(type_id, instance, data):
        resources.append(Resource(type_id, group, instance, data))

    def d(type_id, instance):
        return donor[(type_id, instance)]

    dialog_strings = []
    ttas_strings = [cat_name]
    about_idx = len(dialog_strings)
    dialog_strings.append(
        f'{cat_name}\r\rClick a category, then an item, to mark it done or '
        'not done. Progress is shared across every household in this '
        'neighborhood. Use Import Spreadsheet Progress once per '
        'neighborhood to bring in the pre-checked items.')
    guardfail_idx = len(dialog_strings)
    dialog_strings.append(
        'Tracker safety stop: a token scan ran too long and was aborted. '
        'Nothing was changed. Please report this.')
    stub_idx = len(dialog_strings)
    dialog_strings.append(DIAG_FACTS['stub'])
    diag_strs = {}
    for key, text in DIAG_FACTS.items():
        if key == 'stub':
            continue
        diag_strs[key] = len(dialog_strings)
        dialog_strings.append(text)

    entries = []
    trees = []
    for i, (item_id, cat, name, _done) in enumerate(items):
        done_idx = len(dialog_strings)
        dialog_strings.append(f'{name}\r\rStatus: DONE!')
        dialog_strings.append(f'{name}\r\rStatus: not done yet.')
        ttas_idx = len(ttas_strings)
        ttas_strings.append(f'{cat}.../{name}')
        tree_id = T_TOGGLE_BASE + i
        if DIAG_MODE:
            trees.append((tree_id, stub_tree(f'Toggle - {name}'[:63], stub_idx)))
        else:
            trees.append((tree_id, toggle_tree(f'Toggle - {name}'[:63], item_id,
                                               done_idx, done_idx + 1)))
        entries.append((tree_id, T_GUARD, ttas_idx))

    view_idx = len(dialog_strings)
    dialog_strings.append(
        f'{cat_name}\r\rItems done (all trackers): $Local:2 of 149')
    view_ttas = len(ttas_strings)
    ttas_strings.append('View Progress...')
    about_ttas = len(ttas_strings)
    ttas_strings.append('About...')
    entries.append((T_VIEW, T_GUARD, view_ttas))
    entries.append((T_ABOUT, T_GUARD, about_ttas))

    if seed_ids:
        import_done_idx = len(dialog_strings)
        dialog_strings.append(f'Imported {len(seed_ids)} completed items '
                              'from the spreadsheet.')
        import_already_idx = len(dialog_strings)
        dialog_strings.append('Spreadsheet progress was already imported in '
                              'this neighborhood; nothing to do.')
        stage_strs = {}
        for stage, text in TEST_STAGES.items():
            stage_strs[stage] = len(dialog_strings)
            dialog_strings.append(text)
        import_ttas = len(ttas_strings)
        ttas_strings.append('Import Spreadsheet Progress...')
        test_ttas = len(ttas_strings)
        ttas_strings.append('Test Token Store...')
        entries.append((T_IMPORT, T_GUARD, import_ttas))
        entries.append((T_TEST, T_GUARD, test_ttas))
        if DIAG_MODE:
            add(TYPE_BHAV, T_IMPORT,
                stub_tree('Interaction - Import Spreadsheet', stub_idx))
            add(TYPE_BHAV, T_TEST, diag_tree(diag_strs))
        else:
            add(TYPE_BHAV, T_IMPORT, import_tree(seed_ids, import_done_idx,
                                                 import_already_idx))
            add(TYPE_BHAV, T_TEST, test_tree(stage_strs, guardfail_idx))

    donor_init = d(TYPE_BHAV, 0x1000).data
    add(TYPE_BHAV, T_INIT, init_tree(donor_init[76:76 + 23]))
    add(TYPE_BHAV, T_MAIN, d(TYPE_BHAV, 0x1001).data)
    add(TYPE_BHAV, T_GUARD, bhav('Guard - Always', [
        expr(1, 1, EXPR_EQ, OWNER_LITERAL, OWNER_LITERAL, t=RET_TRUE, f=RET_TRUE)
    ], argc=1))
    add(TYPE_BHAV, T_VIEW, view_tree(view_idx, all_item_ids))
    add(TYPE_BHAV, T_ABOUT, bhav('Interaction - About', [
        instr(OP_DIALOG, RET_TRUE, RET_TRUE, dialog_ops(about_idx))
    ], argc=1))
    for tree_id, data in trees:
        add(TYPE_BHAV, tree_id, data)

    add(TYPE_TTAB, 1, ttab(ttab_entry_template(d(TYPE_TTAB, 1).data), entries))
    add(TYPE_TTAS, 1, str_resource('Pie Menu Strings', ttas_strings))
    add(TYPE_STR, 0x12D, str_resource('Dialog prim string set', dialog_strings))
    add(TYPE_STR, 0x85, d(TYPE_STR, 0x85).data)
    add(TYPE_CTSS, 0x7D0, str_resource('', [
        cat_name,
        'Neighborhood-wide checklist. Click to check items off; progress is '
        'shared across all households. State is stored in the neighborhood, '
        'not the lot.']))
    add(TYPE_GLOB, 1, d(TYPE_GLOB, 1).data)
    add(TYPE_SLOT, 0x80, d(TYPE_SLOT, 0x80).data)
    add(0x856DDBAC, 5, d(0x856DDBAC, 5).data)
    add(TYPE_OBJF, 0x41A7, d(TYPE_OBJF, 0x41A7).data)
    add(TYPE_NREF, 0x41A7, f'REBECCATRACKER{key.upper()}'.encode('latin-1'))
    add(TYPE_OBJD, 0x41A7, patch_objd(
        d(TYPE_OBJD, 0x41A7).data,
        filename=f'{cat_name} by Rebecca', guid=guid, attr_count=0, price=0))


def build_token_object(resources, donor, all_item_ids):
    """One hidden token OBJD per item GUID (plus test + sentinel), all in a
    single group with shared support resources — ACR's multi-OBJD-per-group
    token pattern. Each OBJD gets a matching OBJf at the same instance."""
    def add(type_id, instance, data):
        resources.append(Resource(type_id, TOKEN_GROUP, instance, data))

    def d(type_id, instance):
        return donor[(type_id, instance)]

    donor_init = d(TYPE_BHAV, 0x1000).data
    add(TYPE_BHAV, T_INIT, init_tree(donor_init[76:76 + 23]))
    add(TYPE_BHAV, T_MAIN, d(TYPE_BHAV, 0x1001).data)
    add(TYPE_STR, 0x85, d(TYPE_STR, 0x85).data)
    add(TYPE_CTSS, 0x7D0, str_resource('', [
        'Tracker Data Token',
        'Invisible data object for the achievement trackers.']))
    add(TYPE_GLOB, 1, d(TYPE_GLOB, 1).data)
    add(TYPE_SLOT, 0x80, d(TYPE_SLOT, 0x80).data)
    add(0x856DDBAC, 5, d(0x856DDBAC, 5).data)
    add(TYPE_NREF, 0x41A7, b'REBECCATRACKERDATATOKENS')

    objf = d(TYPE_OBJF, 0x41A7).data
    objd = d(TYPE_OBJD, 0x41A7).data
    for n, item_id in enumerate(sorted(set(all_item_ids) | {TEST_ID, SENTINEL_ID})):
        inst = 0x41A7 + n
        add(TYPE_OBJF, inst, objf)
        add(TYPE_OBJD, inst, patch_objd(
            objd, filename=f'Rebecca Tracker Token {item_id:03d}',
            guid=item_guid(item_id), attr_count=0, price=0, hidden=True))


def main():
    donor = {(r.type_id, r.instance_id): r
             for r in read_all_resources(PLAQUE_DONOR)}
    plaques = load_items()
    all_items = [it for key in ('ach', 'ltw', 'custom') for it in plaques[key]]
    seed_ids = [item_id for item_id, _, _, done in all_items if done]
    print(f"{len(all_items)} items across {len(PLAQUES)} plaques; "
          f"{len(seed_ids)} pre-seeded: {seed_ids}")

    all_item_ids = [item_id for item_id, *_ in all_items]
    resources = []
    for key, group, guid, cat_name in PLAQUES:
        # Import/Test interactions live on the Achievements plaque only
        build_plaque(resources, donor, key, group, guid, cat_name,
                     plaques[key], all_item_ids,
                     seed_ids=seed_ids if key == 'ach' else None)
    build_token_object(resources, donor, all_item_ids)

    write_package(OUTPUT, resources)
    print(f'wrote {OUTPUT.name}: {len(resources)} resources, '
          f'{OUTPUT.stat().st_size} bytes')

    # verify: everything reparses, per-group sanity
    reread = read_all_resources(OUTPUT)
    assert len(reread) == len(resources)
    groups = {}
    for r in reread:
        groups.setdefault(r.group_id, []).append(r)
        if r.type_name == 'BHAV':
            s2parser.parse_bhav(r.data)
    for g, rs in sorted(groups.items()):
        kinds = {}
        for r in rs:
            kinds[r.type_name] = kinds.get(r.type_name, 0) + 1
        objd = next(r for r in rs if r.type_name == 'OBJD')
        w = struct.unpack_from('<108H', objd.data, 64)
        print(f'  group {g:08x}: guid={w[15]:04x}{w[14]:04x} type={w[9]} '
              f'{sum(kinds.values())} res {kinds}')


if __name__ == '__main__':
    main()
