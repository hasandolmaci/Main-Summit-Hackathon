/* Sample shortlist for the demo page. Every company here is made up.
   Shape mirrors one row of the radar run output:
   score (0-1), top-3 reasons (signal code + plain-English text), most similar past winner. */
window.SUNBEAM_SAMPLE = {
  run: { date: '2026-11-20', scanned: 1240, shortlisted: 10 },
  rows: [
    { name: 'Seraph Spectral', place: 'Zurich', what: 'hyperspectral imaging', sector: 'deeptech', score: 0.91,
      reasons: [['PAT', '2 patents published'], ['GRANT', 'Horizon Europe grant'], ['TM', 'EUIPO trademark']], twin: 'Lichtfeld AI' },
    { name: 'Korund Quantum', place: 'Innsbruck', what: 'trapped-ion hardware', sector: 'deeptech', score: 0.88,
      reasons: [['GRANT', 'Horizon Europe grant'], ['PAT', 'Patent published'], ['GH', 'Activity 1.9× up']], twin: 'Ionwerk' },
    { name: 'Lumerion Photonics', place: 'Jena', what: 'photonics spinout', sector: 'deeptech', score: 0.87,
      reasons: [['GRANT', 'Horizon Europe grant'], ['PAT', 'EP patent published'], ['GH', 'Activity 3.1× up']], twin: 'Kryonix Optics' },
    { name: 'Vaultic Robotics', place: 'Munich', what: 'warehouse robotics', sector: 'deeptech', score: 0.82,
      reasons: [['PAT', 'Patent published'], ['TM', 'EUIPO trademark'], ['GH', 'Outside contributors up']], twin: 'Gripwerk' },
    { name: 'Mirada Bio', place: 'Heidelberg', what: 'lab automation', sector: 'software', score: 0.80,
      reasons: [['TM', 'EUIPO trademark'], ['GH', 'Activity 3.4× up'], ['PAT', 'Patent published']], twin: 'Pipetto' },
    { name: 'Feldspar Materials', place: 'Aachen', what: 'battery materials', sector: 'deeptech', score: 0.79,
      reasons: [['GRANT', 'EIC Pathfinder grant'], ['PAT', 'Patent published'], ['TM', 'Trademark filed']], twin: 'Anodion' },
    { name: 'Nordlicht Grid', place: 'Hamburg', what: 'grid forecasting', sector: 'software', score: 0.77,
      reasons: [['GRANT', 'Horizon Europe grant'], ['GH', 'Outside contributors up'], ['TM', 'Trademark filed']], twin: 'Stromsicht' },
    { name: 'Kaltwerk Cryo', place: 'Dresden', what: 'cryogenic sensors', sector: 'deeptech', score: 0.76,
      reasons: [['GRANT', 'Horizon 2020 grant'], ['GH', 'Activity 2.4× up'], ['PAT', 'Patent published']], twin: 'Quellbit' },
    { name: 'Tensile Labs', place: 'Berlin', what: 'ML infra, open source', sector: 'software', score: 0.74,
      reasons: [['GH', 'Outside contributors up'], ['GH', 'Activity 2.8× up'], ['TM', 'Trademark filed']], twin: 'Vectrolab' },
    { name: 'Aquilo Water', place: 'Karlsruhe', what: 'membrane filtration', sector: 'deeptech', score: 0.71,
      reasons: [['PAT', 'Patent published'], ['GRANT', 'CORDIS project'], ['TM', 'EUIPO trademark']], twin: 'Purafil' }
  ]
};
