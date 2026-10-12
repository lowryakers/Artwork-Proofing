"""Formula provenance: a master-list fill weight is used only for the formula
the artwork was approved against.

    python3 tests/test_formula_provenance.py

The beef bottles' live master export still carried pre-reformulation fill
weights: four false net-weight CRITICALs. The approved panel record (ReadyDoc)
names its formula (formula_ref / formula_version); the master row names the
formula its fill weight belongs to. Different, or absent from the master row,
-> "could not evaluate — reference data predates the formula", net weight
NOT_RUN. No formula on the approved record -> compared as before, and said so.

Exits non-zero on any failure. No test framework, no network, no vision API.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proof_engine as pe  # noqa: E402
import app as A  # noqa: E402

APPROVED = {'version': 1, 'status': 'approved', 'formula_ref': 'F-00069', 'formula_version': '1.2',
            'panel': {'serving_size_g': 35.0, 'servings_per_container': 1.0}}


def _run():
    fails = []

    def check(name, cond):
        print(f'[{"ok  " if cond else "FAIL"}] {name}')
        if not cond:
            fails.append(name)

    row = A._parse_master_csv('SKU,Product name,GTIN,Formula ref,Formula version,Fill weight (g)\n'
                              'BEF-BTL-CSG,Cinnamon Sugar,850079939226,F-00069,1.2,34.503\n')[0]
    check('master parser reads Formula ref / Formula version',
          row['formula_ref'] == 'F-00069' and row['formula_version'] == '1.2')

    p = pe._formula_provenance(row, APPROVED)
    check('same formula -> match', p['state'] == 'match' and 'F-00069 v1.2' in p['note'])
    p = pe._formula_provenance(dict(row, formula_ref=' f-00069 ', formula_version='v1.20'), APPROVED)
    check('case, spacing, "v" and trailing zero do not make a mismatch', p['state'] == 'match')

    p = pe._formula_provenance(dict(row, formula_version='1.1'), APPROVED)
    check('older formula version on the master row -> mismatch, "predates the formula"',
          p['state'] == 'mismatch' and 'predates the formula' in p['note']
          and 'F-00069 v1.1' in p['note'] and 'F-00069 v1.2' in p['note'])
    p = pe._formula_provenance(dict(row, formula_ref='', formula_version=''), APPROVED)
    check('blank formula on the master row -> mismatch (the fill weight is unproven)',
          p['state'] == 'mismatch' and 'no formula recorded' in p['note'])

    p = pe._formula_provenance(row, {'version': 1, 'status': 'approved', 'panel': {}})
    check('approved record names no formula -> not_recorded, said in the note',
          p['state'] == 'not_recorded' and 'provenance not recorded' in p['note'])
    p = pe._formula_provenance(row, None)
    check('no approved record at all -> not_recorded', p['state'] == 'not_recorded')

    # Applied to the net-weight check.
    netwt = {'status': 'UNVERIFIED', 'issues': [],
             'notes': ['NOT VERIFIED — no fill weight supplied for this SKU.']}
    pe._apply_formula_provenance(netwt, pe._formula_provenance(dict(row, formula_ref=''), APPROVED))
    check('mismatch -> net weight NOT_RUN, the reason first, no "no fill weight supplied"',
          netwt['status'] == 'NOT_RUN' and netwt['notes'][0].startswith('Could not evaluate')
          and not any('no fill weight supplied' in n for n in netwt['notes']) and netwt['issues'] == [])
    run, skipped = pe._completeness({'netwt': netwt})
    check('...which blocks CLEAN (net weight is skipped)', skipped == ['netwt'])
    check('...and the file reads INCOMPLETE when nothing else is found',
          pe._file_severity([], [], [], [], [], skipped) == 'incomplete')

    netwt = {'status': 'PASS', 'issues': [], 'notes': []}
    pe._apply_formula_provenance(netwt, pe._formula_provenance(row, APPROVED))
    check('match -> status untouched, provenance noted',
          netwt['status'] == 'PASS' and netwt['formula_provenance'] == 'match' and netwt['notes'])

    print()
    if fails:
        print('FAILURES:', *fails, sep='\n  - ')
        return 1
    print('All formula provenance checks pass.')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
