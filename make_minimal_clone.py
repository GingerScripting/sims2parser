#!/usr/bin/env python3
"""Diagnostic: reclone the diploma donor as a new object, changing ONLY the
GUID, filename, NREF, and catalog strings. Every other resource is
byte-identical to the known-working package. If this crashes the game, the
problem is in our DBPF writer/packaging; if it loads, packaging is fine and
any crash in the real tracker comes from generated content."""

from pathlib import Path

from s2object import TYPE_CTSS, TYPE_NREF, TYPE_OBJD, patch_objd, str_resource
from s2writer import Resource, write_package, read_all_resources

DONOR = Path('sample-packages/Christianlov_CounterfeitCollegeDiploma.package')
OUTPUT = Path('TrackerTest_MinimalClone.package')
GUID = 0xB3CCA702  # distinct from the tracker's 0xB3CCA701

resources = []
for r in read_all_resources(DONOR):
    if r.type_id == TYPE_OBJD:
        data = patch_objd(r.data, filename='Tracker Test Minimal Clone',
                          guid=GUID, attr_count=0)
    elif r.type_id == TYPE_NREF:
        data = b'TRACKERTESTMINIMALCLONE'
    elif r.type_id == TYPE_CTSS:
        data = str_resource('', ['Tracker Test (Minimal Clone)',
                                 'Diagnostic clone of the diploma. Safe to delete.'])
    else:
        data = r.data
    resources.append(Resource(r.type_id, r.group_id, r.instance_id, data, r.instance_hi))

write_package(OUTPUT, resources)
print(f'wrote {OUTPUT}: {len(resources)} resources, {OUTPUT.stat().st_size} bytes')
