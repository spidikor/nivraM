"""Generate analytical TEST DATA using the current production serializer; no molecular prediction."""
from dataclasses import replace
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from nivram.populations import (JointState, ModelConditions, ModelProvenance,
                               PopulationModel, sample_grid, population_capabilities)

conditions = ModelConditions(temperature_kelvin=298.15, solvent='water', ionic_strength_mol_l=None,
    activity_convention='Synthetic analytical convention', reference_ph=7.0, ph_min=0, ph_max=14,
    energy_unit='kJ/mol', energy_basis='aqueous_transformed_gibbs_at_reference_ph',
    standard_state='Synthetic common standard', solvation_model='Synthetic input only',
    thermal_entropy_treatment='Included by synthetic assertion', degeneracy_convention='explicit',
    sampling_method='Two synthetic states', truncation_treatment='Explicit synthetic fixture')
provenance = ModelProvenance(input_identity='synthetic-not-a-drawing', engine='synthetic-test',
    engine_version='1', model_name='two-state arithmetic', model_version='1',
    source='Synthetic analytical test data only', created_utc='2026-09-20T00:00:00Z',
    applicability='Mathematics test only', validation='No molecular validation',
    source_kind='synthetic_analytical')
states = tuple(JointState(state_id=sid, protonation_id=sid, tautomer_id='t0',
    stereoisomer_id='fixed', conformer_id='c0', structure_id='synthetic-' + sid,
    atom_ids=('a0', 'a1'), formal_charge=protons - 1, relative_protons=protons,
    free_energy_at_reference=0) for sid, protons in (('HA', 1), ('A', 0)))
model = PopulationModel(states=states, conditions=conditions, provenance=provenance,
    reference_state_id='HA', supported_classes=('protonation',), equilibrium_scope='conditional_configuration',
    equilibrium_justification='Synthetic fixed configuration', ensemble_complete=True)

def envelope(value, **kwargs):
    binding = dict(documentRevision=7, graphHash='a' * 64, modelSignature=value.signature)
    return {'binding': binding, 'envelope': {'schemaVersion': 1, **binding,
        'model': value.to_dict(), 'samples': [row.to_dict() for row in sample_grid(value, **kwargs)]}}

data = {'capabilities': population_capabilities(), 'complete': envelope(model),
    'incomplete': envelope(replace(model, ensemble_complete=False),
        omitted_fractions={70: 0.2}, omitted_mass_source='Synthetic pH 7 mass assertion only'),
    'narrow': envelope(replace(model, conditions=replace(conditions, reference_ph=7.4, ph_min=7.35, ph_max=7.45)))}
destination = HERE / 'population-analytical.json'
with destination.open('x', encoding='utf-8') as stream:
    json.dump(data, stream, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
print(json.dumps({'status': 'written', 'path': str(destination), 'kind': 'synthetic analytical test data',
                  'samples_per_envelope': 141, 'envelopes': 3}))
