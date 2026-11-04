"""Translate source dialogue selection records using declared engine contracts.

No NPC, quest or dialogue IDs belong here. Source numeric IDs are resolved to
names before validating the target registry. A renamed skill requires an
explicit adapter supplied by the caller, never a guessed successful check.
"""
import xml.etree.ElementTree as ET
import json
from pathlib import Path
import zipfile

from campaign_dialogue import unique_index
from campaign_sources import retail_sources
from upgrade_map import read


def load_contract(source, target, skill_adapters=None):
    """Read actual installed registries and native authored skill usages."""
    sources = retail_sources(source, {
        'types': ('Scripts.pak', 'Libs/AI/TypeDefinitions.xml'),
        'difficulties': ('Tables.pak', 'Libs/Tables/rpg/skill_check_difficulty.xml'),
        'reputations': ('Tables.pak', 'Libs/Tables/rpg/reputation_change.xml')})
    rows = {key: [e.attrib for e in ET.fromstring(sources[key]['data']).findall('./table/rows/row')]
            for key in ('difficulties', 'reputations')}
    with zipfile.ZipFile(Path(target) / 'Data/Tables.pak') as archive:
        native = {key: [e.attrib for e in ET.fromstring(read(archive, 'Libs/Tables/rpg/' + name + '.xml')).iter(name)]
                  for key, name in [('native_difficulties', 'skill_check_difficulty'),
                                    ('native_reputations', 'reputation_change')]}
    return dict(source_types=SelectionCompiler.source_types(sources['types']['data']),
                **rows, **native, native_skills=native_skill_checks(target), skill_adapters=dict(skill_adapters or {}))


_NATIVE_SKILLS = {}


def native_skill_checks(target):
    """SkillCheckType values authored in native quests; Scripts.pak is scanned once per build."""
    path = Path(target) / 'Data/Scripts.pak'
    stat = path.stat()
    key = (path.resolve(), stat.st_size, stat.st_mtime_ns)
    if key not in _NATIVE_SKILLS:
        skills = set()
        with zipfile.ZipFile(path) as archive:
            for entry in archive.namelist():
                if not entry.lower().startswith('quests/') or not entry.lower().endswith('.xml'):
                    continue
                blob = read(archive, entry)
                if b'SkillCheckType=' not in blob:
                    continue
                for sequence in ET.fromstring(blob).iter('Sequence'):
                    if sequence.get('Type') == 'Skillcheck' and sequence.get('SkillCheckType'):
                        skills.add(sequence.get('SkillCheckType'))
        _NATIVE_SKILLS.clear()
        _NATIVE_SKILLS[key] = sorted(skills)
    return list(_NATIVE_SKILLS[key])


class SelectionCompiler:
    @classmethod
    def from_contract(cls, path):
        """Load a reviewable source/target ABI contract, not NPC-specific rules."""
        return cls(**json.loads(Path(path).read_text(encoding='utf-8')))

    def __init__(self, source_types, difficulties, reputations,
                 native_difficulties, native_reputations, native_skills, skill_adapters=None,
                 preserve_reputation_caps=False):
        self.types = dict(source_types)
        self.difficulties = unique_index(difficulties, 'skill_check_difficulty_id')
        self.reputations = unique_index(reputations, 'reputation_change_id')
        self.native_difficulties = unique_index(native_difficulties, 'skill_check_difficulty_name')
        self.native_reputations = unique_index(native_reputations, 'name')
        self.native_skills = set(native_skills)
        self.adapters = dict(skill_adapters or {})
        self.reputation_upgrades = {}
        if preserve_reputation_caps:
            # Stable across all conversations using this source/target snapshot.
            next_id = max((int(r['reputation_change_id']) for r in native_reputations), default=0) + 1
            for source in sorted(reputations, key=lambda r: int(r['reputation_change_id'])):
                native = self.native_reputations.get(source['name'])
                if native is None or source.get('reputation_cap', '') == native.get('reputation_cap', ''):
                    continue
                # Only the cap has a direct schema correspondence. Changed
                # targets or notifications require their own dependency adapter.
                if any(source.get(k, '').lower() != native.get(k, '').lower() for k in
                       ('change', 'can_change_hostility', 'reputation_change_target_id', 'reputation_notification_id')):
                    continue
                row = {k: v for k, v in source.items() if v != ''}
                row.update(name='gm_kcd1_' + source['name'], reputation_change_id=str(next_id),
                           can_change_hostility=source['can_change_hostility'].lower())
                if row['name'] in self.native_reputations:
                    raise ValueError('Imported reputation name already exists: ' + row['name'])
                next_id += 1
                self.reputation_upgrades[source['reputation_change_id']] = row

    def resources(self):
        if not self.reputation_upgrades:
            return {}
        root = ET.Element('database', name='barbora')
        rows = ET.SubElement(root, 'reputation_changes', version='1')
        for row in self.reputation_upgrades.values():
            ET.SubElement(rows, 'reputation_change', row)
        return {'Libs/Tables/rpg/reputation_change__gm_dialogue.xml': ET.tostring(root, encoding='utf-8', xml_declaration=True)}

    @staticmethod
    def source_types(blob):
        definitions = [e for e in ET.fromstring(blob).iter('Enum') if e.get('name') == 'sequenceType']
        if len(definitions) != 1:
            raise ValueError('Missing or ambiguous source sequenceType enum')
        result = {}
        for member in definitions[0]:
            value = member.get('value')
            if value is None or value in result:
                raise ValueError('Invalid source sequenceType enum')
            result[value] = member.tag
        return result

    def compile(self, source, entry_condition):
        if source['group'] != '0':
            raise ValueError('Unconverted dialogue group: ' + source['group'])
        kind = self.types.get(source['type'])
        if kind is None:
            raise ValueError('Unknown source sequence type: ' + source['type'])
        attrs = {}
        difficulty_id = source['skill_check_difficulty_id']
        if kind != 'default':
            skill = self.adapters.get(kind, kind)
            if skill not in self.native_skills:
                raise ValueError('Unconverted dialogue sequence type: ' + kind)
            difficulty = self.difficulties.get(difficulty_id)
            if difficulty is None:
                raise ValueError('Missing source dialogue difficulty: ' + difficulty_id)
            name = difficulty['skill_check_difficulty_name']
            if name not in self.native_difficulties:
                raise ValueError('Missing native dialogue difficulty: ' + name)
            attrs.update(Type='Skillcheck', SkillCheckType=skill, SkillCheck=name)
        elif difficulty_id:
            raise ValueError('Default sequence unexpectedly has a skill difficulty')
        reputation_id = source['reputation']
        if reputation_id != '0':
            reputation = self.reputations.get(reputation_id)
            if reputation is None:
                raise ValueError('Missing source reputation change: ' + reputation_id)
            name = reputation['name']
            target = self.native_reputations.get(name)
            if reputation_id in self.reputation_upgrades:
                target = self.reputation_upgrades[reputation_id]
                name = target['name']
            if target is None:
                raise ValueError('Missing native reputation change: ' + name)
            # A shared name alone does not establish equivalent effects.
            for key in ('change', 'can_change_hostility', 'reputation_cap',
                        'reputation_change_target_id', 'reputation_notification_id'):
                a, b = reputation.get(key, '').lower(), target.get(key, '').lower()
                if a != b:
                    raise ValueError('Reputation effect requires conversion: ' + name + '.' + key)
            attrs['Reputation'] = name
        if entry_condition.strip() in ('skillCheckSuccess', 'skillCheckFail'):
            attrs['SkillcheckCondition'] = {'skillCheckSuccess': 'Success', 'skillCheckFail': 'Fail'}[entry_condition.strip()]
            entry_condition = ''
        return attrs, entry_condition
