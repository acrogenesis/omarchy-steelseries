// Run with node tests/test_panel.js; no dependencies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const model = {};
vm.createContext(model);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../PanelModel.js'), 'utf8'), model);
const markup = '<img src="https://example.invalid/tracker"> & <b>audio</b>';
assert.equal(model.safeLabel(markup), '&lt;img src="https://example.invalid/tracker"&gt; &amp; &lt;b&gt;audio&lt;/b&gt;');
const options = model.outputOptions([
  {name: 'unsafe', label: markup, available: true},
  {name: 'hdmi', label: 'Digital Stereo (HDMI) [Odyssey G95SC]', available: true},
  {name: 'disconnected', label: markup, available: false},
], 'missing', 'Do not switch', 'Unavailable', 'HDMI · %1');
assert.equal(options[1].label, model.safeLabel(markup));
assert.equal(options[2].label, 'HDMI · Odyssey G95SC');
assert.equal(options[3].value, 'missing');
assert.equal(options[3].label, 'Unavailable');
assert.equal(options.length, 4);
console.log('Panel label safety and output options passed');
