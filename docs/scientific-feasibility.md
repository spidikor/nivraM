# maraPssiwS scientific contract and feasibility record

Evidence reviewed: 2026-09-19. Scope: aqueous pH-state analysis. This component
provides population mathematics and an evidence-qualified physiology dataset.
**No molecular population predictor is integrated or scientifically validated.**
Protonation, tautomer, R/S and E/Z, and conformer/rotamer prediction requirements
remain unmet. A successful synthetic calculation does not change those statuses.
No engine was purchased, licensed, installed, or submitted a structure in this
workstream. There are no network calls in `nivram/populations.py`.

## What is implemented

- `JointState` preserves supplied state, protonation, tautomer, stereo,
  conformational-basin and structure identifiers, atom correspondence, formal
  charge, relative proton count, degeneracy and reference free energy.
- `ModelConditions` requires water, actual temperature in kelvin, an ionic-strength
  value or explicit absence, activity convention, standard state, energy units,
  reference pH, declared pH domain, and solvation/entropy/sampling/truncation
  treatment. These declarations are caller assertions, not verified chemistry.
- `ModelProvenance` requires input identity, engine/model versions, source,
  timestamp, applicability, validation statement and transformation history.
  Synthetic analytical fixtures must identify themselves as such.
- `PopulationModel` requires a common reference and an explicit completeness and
  equilibrium-scope declaration. It rejects duplicate basin identities, mismatched
  mapped cores, inconsistent charge/proton differences, and implicit mixtures of
  unsupported state classes. Configuration-conditional models cannot claim
  between-configuration fractions.
- `model_from_microscopic_equilibria` resolves supplied microscopic pKa and/or
  reference-pH state-ratio edges. It rejects disconnected state families,
  non-single-proton pKa edges and inconsistent thermodynamic cycles. This is a
  constructor from external data, not a pKa estimation method.
- `evaluate_populations` uses stable shifted log weights. `aggregate_populations`
  sums the same ensemble; `visible_populations` never rescales a displayed subset.
- `sample_grid` returns absolute integer tenths, normally indices 0 through 140.
  Unsupported samples contain `result: null` and `outside_model_domain`. No
  extrapolation or mouse-triggered external computation is implemented here.
- `population_capabilities()` returns explicit unavailable scientific state-class
  statuses. A supplied model may be evaluated, but its status is only
  `supplied_model_only`, never an installed validated predictor.

The mathematical module uses only the Python standard library. It writes no
files and does not modify a drawing. A separate bounded local worker now connects
complete supplied models to the server and an interactive chart/table. The
integration retains input identity, model signature, actual conditions,
normalization scope and warnings. It does not infer states or energies from the
drawing, enable a scientific predictor, or verify the model's correspondence to
the drawing. See [Supplied population models](supplied-populations.md) for the
file format, workflow and scoped verification.

## Mathematical convention and limits

For state i, the input energy is an aqueous transformed Gibbs energy at a common
reference pH and the declared temperature/standard state:

    G_i(pH) = G_i(reference_pH) + n_H,i R T ln(10) (pH - reference_pH)
    log_weight_i = -G_i(pH)/(R T) + ln(g_i)
    P_i = exp(log_weight_i - log_sum_exp(log_weights))

A shared additive energy/proton-count offset cancels. Increasing pH disfavors a
state containing more exchangeable protons. J/mol, kJ/mol and kcal/mol are accepted
explicitly; eV, raw MMFF potential energies and unqualified electronic energies
are not accepted as the declared energy basis. Numerical checks cannot detect a
caller falsely relabeling an energy, an identity or a validation claim.

`degeneracy_convention: explicit` counts the supplied positive integer multiplicity
once. With `included_in_free_energy`, every external degeneracy must be 1.
Duplicate embeddings are rejected by their supplied joint-basin identity; the
upstream adapter still owns stereo-preserving chemical identity and conformer
deduplication. Unique labels alone cannot prove that two structures are distinct.
The same atomic-core IDs can have different ordering; exchangeable protons are
tracked by stoichiometry instead of forcing identical explicit-hydrogen arrays.

An `EquilibriumRatioEdge` means log10(P_target/P_source) at reference pH, including
state multiplicity. A `MicroscopicPKaEdge(HA, A, pKa)` means A/HA = 10^(pH-pKa),
also including that state's multiplicity. The constructor removes explicit
degeneracy while recovering free energies so that evaluation does not add it twice.
It does not silently repair inconsistent cycles or normalize disconnected stereo
families. A general multi-proton equilibrium may be represented by a justified
reference-ratio edge; a single pKa edge must exchange exactly one proton.

A declared complete model normalizes its declared ensemble. Completeness remains
a caller assertion. An incomplete model with unknown omitted mass returns
`conditional_on_represented_states`; its 100% refers only to represented states.
For known omitted mass, supply an independently sourced fraction for that exact
pH. Represented fractions then sum to 1 minus omitted mass. Per-grid omissions
are keyed by integer sample index; an omitted mass at one pH is not silently
reused across the plot. The result retains underflowed populations' finite natural
log fractions when floating-point fractions become zero.

A fixed-configuration result is conditional even when its protonation ensemble
is complete. A single listed R state cannot establish that a real sample is 100%
R. Unsupported tautomer/stereo/conformer aggregate requests are rejected. pH
alone cannot identify a kinetically persistent mixture's starting composition.
This component neither models interconversion kinetics nor produces MD topology
or force-field parameter files.

The temperature is part of the supplied energy model. Changing a temperature field
does not establish valid thermodynamics at the new temperature; adapters must
recompute or provide a justified temperature dependence. A provider result at
298.15 K cannot be relabeled as the requested 310.15 K. The pH grid spacing is
display resolution, not a predictive-accuracy claim.

This formulation is consistent with the microstate free-energy/proton-count
relationship described by [Gunner et al.](https://link.springer.com/article/10.1007/s10822-020-00280-7).
Conformer populations require adequately sampled, compatible solution free
energies with thermal and entropy treatment; see the primary conformer-ensemble
study [Grimme et al., 2021](https://doi.org/10.1021/acs.jpca.1c00971).
RDKit embedding frequencies and an MMFF minimization energy list do not supply
that complete contract.

## Bounded engine assessment: three candidates, no deployment claims

This is a documentary capability assessment. Actual output below means output
produced in this workstream; all three have **none**. No provider license or
credentials have been inferred from documentation or from installed plugins.

| Candidate stack | Documented capability and exact evidence scope | Access, version, Windows/cost evidence | Missing gate / result |
| --- | --- | --- | --- |
| ChemAxon microspecies engine plus its documented tautomer calculations | Current pKa documentation describes micro/macro pKa, microspecies curves, temperature input and a 0.1 mol/L ionic-strength convention. The display switches to macrostates above eight ionizable atoms and omits very small displayed species. Separate tautomer API output does not prove a coupled full ensemble. | pKa documentation dated 2026-09-01; no installed engine/version, license, validated API response, Windows integration or measured cost established. Commercial access requires its own lawful entitlement. | Actual output: none. Need licensed runtime/API, exact output contract, truncation metadata and condition-matched validation. No stereo/conformer population claim is established. |
| CREST/CENSO with an explicitly specified aqueous free-energy refinement workflow | CREST 3.1 documents conformer searching, implicit solvation and a Hessian/free-energy re-ranking option. Thermal, solvation, basin multiplicity, stereochemical identity and convergence must be preserved by the complete stack. | CREST 3.1 is documented; no exact deployed CREST/CENSO/QM stack has been pinned or run. Every component's license and Windows execution path remain to be assessed. Runtime/cost not measured. | Actual output: none. Need qualified aqueous energies for every state, adequate sampling and a compatible connection between different proton counts. This stack is not a ready-made general pH/stereo predictor. |
| Uni-pKa research model and its microstate enumerator | Official repository describes a microstate enumerator and a learned structure free-energy model. This is a relevant research alternative, not demonstrated support for every requested state-class fraction. | Repository identifies Apache-2.0 for code and recommends a Docker/PyTorch/Uni-Core environment with RDKit 2022.9.3. No checkpoint identity, weight rights, exact commit, Windows deployment or measured runtime established here. Do not replace the editor's pinned RDKit environment to try it. | Actual output: none. Need isolated reproducible inference, checkpoint and data provenance, state-class coverage and independent validation. Quantitative tautomer/stereo/conformer support remains unproven. |

Primary/official capability sources, accessed 2026-09-19:

- [ChemAxon pKa plugin](https://docs.chemaxon.com/latest/calculators_pka-plugin.html)
  and [tautomer API](https://apidocs.chemaxon.com/python_api/apidocs/chemaxon/calculations/tautomer.html).
- [CREST 3.1 release documentation](https://crest-lab.github.io/crest-docs/page/releases/release_current.html),
  [free-energy versus electronic-energy options](https://crest-lab.github.io/crest-docs/page/documentation/keywords.html),
  and [CENSO documentation](https://xtb-docs.readthedocs.io/en/latest/CENSO_docs/censo.html).
- [Official Uni-pKa repository](https://github.com/dptech-corp/Uni-pKa)
  and its [primary study](https://doi.org/10.1021/jacsau.4c00271).

SwissParam is historical inspiration, not a fourth candidate or a configured
endpoint. Its [current homepage](https://www.swissparam.ch/) states that the
service no longer relies on ChemAxon and presents parameterization workflows.
The 2023 article's former microspecies functionality is not proof of its current
availability. No parameterization output is substituted for population analysis.

Stop candidate hunting at these three unless scope changes. A demonstrated
transient failure may receive one retry per candidate; a missing license, model,
compatible output or validation set is a specific unmet gate, not a reason to
repeatedly submit. No private molecule, image, project, archive or credential was
sent to a provider. Online providers remain disabled.

## Physiology schema v1 and curated evidence

`nivram/data/physiology.json` contains 19 selectable contexts and six primary
source records. Eight presets contain numerical evidence; eleven are explicit
gaps. `load_physiology_presets()` reads the bundled file and validates it, returning
a fresh object. No filesystem path supplied by a browser or provider is accepted.

Every preset preserves compartment and anatomy, species, population/preparation,
condition, temperature or its explicit absence, method, handling, source IDs,
statistical meaning, evidence limitations, and a display type. The nested `ph`
record has exactly `value`, `lower`, `upper`, and `confidence_level`; unavailable
records have `ph: null`. Unspecified plus/minus statistics remain unplotted
`reported_uncertainty` metadata. The validator rejects a manufactured band around
a source point, a numerical evidence-gap record, incompatible statistical display
semantics, duplicate IDs and missing provenance.

| Context | Curated representation and limitation |
| --- | --- |
| Plasma/blood group | Arterial whole-blood reference interval 7.35-7.45 for the laboratory's age category at least six months. The specimen is not separately isolated plasma. Exact endpoints bypass the 0.1 sample grid. |
| Neuronal cytosol | Reported resting pHi 7.09 from 20 cells in surgical tissue from five adults with temporal-lobe epilepsy. The central-statistic definition is unverified from the inspected abstract. Its reported +/-0.14 is retained with unspecified statistic and not plotted. No healthy-human interval is inferred. |
| CSF | Historical normal-subject mean 7.31 with reported 99% CI 7.29-7.33, n=12. Render as uncertainty of the mean, not a reference interval. |
| Synaptic cleft | Explicit numerical gap. Neither vesicle-lumen nor bath pH is substituted. |
| Nephron luminal fluid | Five reported rat pH values: superficial late proximal 6.78, deep loop bend 7.39, original-study late distal 6.59, papillary collecting-duct base 6.24 and tip 6.12. Central-statistic definitions, plus/minus types and detailed protocol fields remain explicitly unresolved from the inspected abstract. |
| Other nephron segments | Separate gaps for Bowman's space, early/deep proximal sites, thin descending/ascending and thick ascending limbs, early distal, connecting tubule, cortical and outer-medullary collecting ducts. No final-urine overlay is present. |

The rat deep-loop and superficial proximal observations must not be joined into
one measured trajectory. Their ordering also precludes imposing universal
monotonic acidification. Historical late-distal nomenclature is retained rather
than silently mapped onto modern DCT anatomy. In-situ and equilibrium tubular-fluid pH are distinct measurement conditions;
equilibrium pH can also be measured in vivo, and carbon-dioxide handling matters
(DuBose 1982). Sources, inspected scopes and missing
methods are stored alongside the values; further source curation can enrich them
without rewriting an unknown SD/SEM, temperature or species as a known fact.

The numerical sources are the [UVM laboratory catalog](https://uvmlabs.testcatalog.org/show/LAB3031-1),
[Bonnet et al.](https://pubmed.ncbi.nlm.nih.gov/30590026/),
[Merril et al.](https://journals.physiology.org/doi/pdf/10.1152/jappl.1961.16.3.485),
and [DuBose et al.](https://pubmed.ncbi.nlm.nih.gov/6405628/).
The additional [collecting-duct handling study](https://www.jci.org/articles/view/110425)
and [synaptic-cleft study](https://pmc.ncbi.nlm.nih.gov/articles/PMC10423045/)
support the stated interpretation boundaries. These overlays cannot validate
aqueous model fractions or establish in-vivo exposure, renal clearance or activity.

## Verification and quantitative release gate

Focused tests use synthetic inputs with analytical expectations, not private or
public molecules disguised as experimentally validated predictions. They cover
the 50:50 pKa point, tenfold ratio direction, coupled two-proton cycles, same-proton
tautomer ratios, units, charge/proton conservation, degeneracy, stereo identity,
duplicate states, disconnected components, very small populations, omitted mass,
aggregation, serialization, bounded grids and exact physiological boundaries.
Schema tests separately check source and statistical semantics. A binary-float
assertion for 0.4 uses an explicit 1e-14 numerical tolerance plus independent
total-mass conservation; no scientific accuracy follows from that tolerance.

Required focused commands from the project root:

    python -m unittest discover -s tests -p test_populations.py -v
    python -m unittest discover -s tests -p test_physiology.py -v

Before any scientific population class changes to available, an adapter must
produce actual condition-matched outputs, preserve this joint-state contract,
and pass independent state-class-specific validation on a frozen evaluation set.
Macro-pKa accuracy does not validate tautomer ratios; enumeration does not validate
stereoisomer fractions. Conformer search expansion must meet the Goal's initial
two-percentage-point aggregate stability criterion, separately from experimental
accuracy. Incomplete support remains visible. Mathematical tests, a plotted curve,
an adapter stub, or a normalized imported table cannot satisfy that release gate.

Observed local Windows check results on 2026-09-19: 22 population tests passed and
12 physiology tests passed, both commands exiting 0 using the task's isolated
Python runtime. Initial missing-module failures established the new acceptance
checks before implementation. Later regression checks reproduced and repaired
identity-alias and unspecified-uncertainty interpretation gaps. No test makes an
empirical molecular-accuracy claim, and provider/native-UI integration was not
exercised by these focused tests.
