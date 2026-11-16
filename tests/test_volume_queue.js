const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const queue = {};
vm.createContext(queue);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../CommandQueue.js'), 'utf8'), queue);
let requests = [];
for (let i = 1000; i >= 0; i--) {
  requests = queue.enqueue(requests, {action: 'volume', channel: 'game', value: i % 101});
}
assert.equal(requests.length, 1);
assert.equal(requests[0].value, 0);
requests = queue.enqueue(requests, {action: 'volume', channel: 'chat', value: 50});
assert.equal(requests.length, 2);
requests = queue.enqueue(requests, {action: 'fallback', value: 'speaker'});
requests = queue.enqueue(requests, {action: 'volume', channel: 'game', value: 70});
assert.equal(requests.length, 4); // A setting change is an ordering barrier.
assert.equal(requests[0].value, 0);
requests = queue.enqueue(requests, {action: 'fallbackVolume', output: 'speaker', value: 35});
requests = queue.enqueue(requests, {action: 'fallbackVolume', output: 'monitor', value: 40});
assert.equal(requests.length, 6); // Device identities must remain separate.
requests = queue.enqueue(requests, {action: 'fallbackVolume', output: 'speaker', value: 20});
assert.equal(requests.length, 6);
assert.equal(requests.at(-1).value, 20);
console.log('Live volume queue coalescing and command ordering passed');
