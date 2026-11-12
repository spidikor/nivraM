/** IUPAC element symbols in atomic-number order; palette scope is separate. */
export const ELEMENT_SYMBOLS=Object.freeze(('H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og').split(' '));
const canonical=new Map(ELEMENT_SYMBOLS.map(symbol=>[symbol.toLowerCase(),symbol]));
export const isElementSymbol=value=>typeof value==='string'&&canonical.get(value.toLowerCase())===value;
export const canonicalElement=value=>typeof value==='string'?canonical.get(value.toLowerCase())??null:null;
export const isElementPrefix=value=>typeof value==='string'&&value.length===1&&ELEMENT_SYMBOLS.some(symbol=>symbol.length===2&&symbol[0].toLowerCase()===value.toLowerCase());
export const COMMON_ELEMENTS=Object.freeze(['C','H','N','O','S','P','F','Cl','Br','I']);
export const MORE_ELEMENTS=Object.freeze(['Li','B','Na','Mg','Al','Si','K','Ca','Mn','Fe','Co','Cu','Zn','As','Se','Tc','Ba','Gd','Pt','Au','Bi','Lu']);
export const ELEMENT_NAMES=Object.freeze({H:'hydrogen',C:'carbon',N:'nitrogen',O:'oxygen',S:'sulfur',P:'phosphorus',F:'fluorine',Cl:'chlorine',Br:'bromine',I:'iodine',Li:'lithium',B:'boron',Na:'sodium',Mg:'magnesium',Al:'aluminium',Si:'silicon',K:'potassium',Ca:'calcium',Mn:'manganese',Fe:'iron',Co:'cobalt',Cu:'copper',Zn:'zinc',As:'arsenic',Se:'selenium',Tc:'technetium',Ba:'barium',Gd:'gadolinium',Pt:'platinum',Au:'gold',Bi:'bismuth',Lu:'lutetium'});
