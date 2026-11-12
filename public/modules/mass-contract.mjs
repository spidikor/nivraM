// Transport validation only. The chemistry engine, not this module, checks its table.
export const MASS_REASON='missing_isotope_mass';
export const MASS_MESSAGE='Molar mass and exact mass are unavailable: the RDKit isotope mass table has no usable entry for one or more explicit isotope labels. The graph annotations are retained; this is not a claim about isotope existence or stability.';
const exact=(o,keys)=>o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join('|')===[...keys].sort().join('|');
export function validMassUnavailable(value,graph){
  if(!exact(value,['reason','message','isotopes'])||value.reason!==MASS_REASON||value.message!==MASS_MESSAGE||!Array.isArray(value.isotopes)||!value.isotopes.length||value.isotopes.length>graph.atoms.length)return false;
  const seen=new Set();
  for(const entry of value.isotopes){
    if(!exact(entry,['symbol','isotope'])||typeof entry.symbol!=='string'||!Number.isInteger(entry.isotope)||entry.isotope<1||entry.isotope>400)return false;
    const key=`${entry.symbol}:${entry.isotope}`;
    if(seen.has(key)||!graph.atoms.some(a=>a.symbol===entry.symbol&&a.isotope===entry.isotope))return false;
    seen.add(key);
  }
  return true;
}
