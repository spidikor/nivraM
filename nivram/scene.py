"""Produce a vector-only scene from the current coordinates.

Element colors are initialized from the supplied reference images. Geometry and
font tuning are provisional; no universal Marvin visual-equivalence claim.
"""
from __future__ import annotations
import math
from rdkit import Chem
from rdkit.Chem.Draw import rdMolDraw2D
from rdkit.Geometry import Point2D
from .chemistry import graph_to_mol, ChemistryError
from .properties import calculate_properties
from .drawing_font import system_arial
from .hash_style import spaced_hashes
from .label_anchors import anchor_element_labels

DEFAULT_STYLE = {'bondWidth': 4.2, 'labelSize': 36, 'bondSpacing': 0.14, 'labelPadding': 0.04, 'color': True,
                 'hashBondStyle': 'engine', 'skeletalCarbons': True}
PALETTE = {
    -1:(0,0,0), 1:(0,0,0), 6:(0,0,0), 7:(0.2,0.2,0.6), 8:(1,0,0),
    9:(0.6,0.4,0), 11:(0,0,1), 15:(1,0.5,0), 16:(0.6,0.6,0),
    17:(0,0.6,0), 19:(0,0.5,0.5), 35:(0.4,0.2,0.2), 53:(0.4,0,0.6)
}

def checked_style(style: dict | None) -> dict:
    if style is not None and not isinstance(style, dict):
        raise ValueError('Invalid drawing-style object.')
    if style is not None and set(style) - set(DEFAULT_STYLE):
        raise ValueError('Unsupported drawing-style fields; nothing was changed.')
    out = DEFAULT_STYLE.copy(); out.update({k:v for k,v in (style or {}).items() if k in out})
    for key, lo, hi in [('bondWidth',0.5,5),('labelSize',10,40),('bondSpacing',0.06,0.28),('labelPadding',0,0.20)]:
        v=out[key]
        if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
            raise ValueError(f'{key} must be between {lo} and {hi}.')
    if out['labelSize'] != int(out['labelSize']):
        raise ValueError('labelSize must be a whole number.')
    if not isinstance(out['color'], bool):
        raise ValueError('Element color must be true or false.')
    if not isinstance(out['skeletalCarbons'], bool):
        raise ValueError('Skeletal carbons must be true or false.')
    if not isinstance(out['hashBondStyle'], str) or out['hashBondStyle'] not in ('engine', 'spaced'):
        raise ValueError('Hashed stereobonds must use Engine spacing or Thicker, spaced bars.')
    return out

def hydrogen_carbon_labels(mol: Chem.Mol, *, skeletal: bool = False) -> dict[int, str]:
    """Expose carbon-bound H counts without creating atoms or changing valence.

    Leave ordinary bonded carbons to RDKit in skeletal mode, including its
    clarifying carbon labels at near-straight junctions. Retain the full
    labels of isolated or annotated carbons. Radical marks remain drawer-owned.
    """
    labels = {}
    for atom in mol.GetAtoms():
        count = atom.GetTotalNumHs()  # excludes separate neighboring H atoms
        if atom.GetAtomicNum() != 6 or not count:
            continue
        isotope = atom.GetIsotope()
        charge = atom.GetFormalCharge()
        atom_map = atom.GetAtomMapNum()
        if skeletal and atom.GetDegree() and not (isotope or charge or atom_map or atom.GetNumRadicalElectrons()):
            continue
        label = (f'<sup>{isotope}</sup>' if isotope else '') + 'CH'
        if count != 1:
            label += f'<sub>{count}</sub>'
        if charge:
            magnitude = str(abs(charge)) if abs(charge) != 1 else ''
            # The custom-label parser draws the ASCII sign as a single glyph.
            label += f'<sup>{magnitude}{"+" if charge > 0 else "-"}</sup>'
        if atom_map:
            label += f':{atom_map}'
        labels[atom.GetIdx()] = label
    return labels

def render_scene(graph: dict, style: dict | None = None) -> dict:
    style = checked_style(style)
    mol = graph_to_mol(graph,allow_empty=True)
    properties = calculate_properties(graph)
    if not mol.GetNumAtoms():
        return {'svg':'<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500" viewBox="0 0 800 500"></svg>',
                'positions':[], 'transform':{'x0':400,'y0':250,'xScale':60,'yScale':-60},
                'formula':'', 'smiles':'','mass':0,'properties':properties,'style':style,'width':800,'height':500}
    # Work on a copy; depiction must never rewrite document coordinates.
    drawing = rdMolDraw2D.PrepareMolForDrawing(Chem.Mol(mol), kekulize=True,
                                              addChiralHs=False, wedgeBonds=True, forceCoords=False)
    # A wedge at a nonstereogenic atom can specify perspective without assigning
    # a stereocenter. RDKit removes these marks during chemistry normalization.
    # Restore an explicitly supplied single-bond mark on the drawing copy only.
    lost_marks = [(bond, item['direction']) for bond, item in zip(drawing.GetBonds(), graph['bonds'])
                  if item.get('direction') in (int(Chem.BondDir.BEGINWEDGE), int(Chem.BondDir.BEGINDASH))
                  and bond.GetBondType() == Chem.BondType.SINGLE and bond.GetBondDir() == Chem.BondDir.NONE]
    if lost_marks:
        possible_centers = {info.centeredOn for info in Chem.FindPotentialStereo(mol)
                            if info.type == Chem.StereoType.Atom_Tetrahedral}
        for bond, direction in lost_marks:
            if bond.GetBeginAtomIdx() not in possible_centers:
                bond.SetBondDir(Chem.BondDir.values[direction])
    xs=[a['x'] for a in graph['atoms']]; ys=[a['y'] for a in graph['atoms']]
    width=max(300,min(6000,math.ceil((max(xs)-min(xs)+4)*65)))
    height=max(220,min(6000,math.ceil((max(ys)-min(ys)+4)*65)))
    font=system_arial()
    drawer=rdMolDraw2D.MolDraw2DSVG(width,height,noFreetype=False)
    opts=drawer.drawOptions();opts.clearBackground=False;opts.padding=0.08
    opts.fontFile=font
    opts.fixedBondLength=65;opts.fixedFontSize=int(style['labelSize']);opts.bondLineWidth=style['bondWidth']
    opts.multipleBondOffset=style['bondSpacing'];opts.addStereoAnnotation=False
    opts.scaleBondWidth=False;opts.singleColourWedgeBonds=False;opts.additionalAtomLabelPadding=style['labelPadding']
    if style['color']:opts.setAtomPalette(PALETTE)
    else:opts.useBWAtomPalette()
    for index, label in hydrogen_carbon_labels(drawing, skeletal=style['skeletalCarbons']).items():
        opts.atomLabels[index] = label
    drawer.DrawMolecule(drawing)
    positions=[]
    for i,a in enumerate(graph['atoms']):
        p=drawer.GetDrawCoords(i);positions.append({'id':a['id'],'x':p.x,'y':p.y})
    p0=drawer.GetDrawCoords(Point2D(0,0));px=drawer.GetDrawCoords(Point2D(1,0));py=drawer.GetDrawCoords(Point2D(0,1))
    drawer.FinishDrawing()
    svg=drawer.GetDrawingText()
    # A wheel without FreeType can silently emit approximated native SVG text.
    # Reject that result instead of restoring inconsistent label/clipping metrics.
    if '<text' in svg:
        raise ValueError('Accurate atom-label outlines require FreeType support; the drawing could not be rendered.')
    svg=svg.replace('stroke-linecap:butt','stroke-linecap:round').replace('stroke-linejoin:miter','stroke-linejoin:round')
    svg=anchor_element_labels(svg, drawing, positions)
    if style['hashBondStyle'] == 'spaced':
        svg = spaced_hashes(svg, drawing, style['bondWidth'])
    return {'svg':svg,'positions':positions,'transform':{'x0':p0.x,'y0':p0.y,'xScale':px.x-p0.x,'yScale':py.y-p0.y},
            'width':width,'height':height,'formula':properties['formula'],
            'mass':(None if properties['molecularWeight']['value'] is None
                    else round(properties['molecularWeight']['value'],3)),
            'smiles':properties['smiles'],
            'properties':properties,'style':style}
