const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// Exercise the actual selector logic without a browser or external dependency.
class Select {
  options = [];
  value = '';
  disabled = true;
  replaceChildren(...options) {
    this.options = options;
    this.value = options[0]?.value || '';
  }
}
const elements = {start: new Select(), end: new Select(), 'example-start':new Select(), 'example-minutes':new Select()};
const source = fs.readFileSync('nextbus/web/experiments.js', 'utf8');
const scope = vm.createContext({
  $: id => elements[id],
  Option: function(text, value) { return {text, value}; },
  serviceTimes: [],
});
vm.runInContext(source.slice(source.indexOf('function timeOptionLabel'),source.indexOf("$('start').onchange=()=>updateWindowEnd();")+"$('start').onchange=()=>updateWindowEnd();".length), scope);
scope.initializeWindowTimes(360, 1440);
assert.equal(elements.start.options[0].text, '0600');
assert.equal(elements.start.options.at(-1).text, '2345');
assert.equal(elements.start.value, '420');
assert.equal(elements.end.value, '570');
assert.equal(elements.end.options.at(-1).text, '2400');
assert.ok(elements.start.options.some(o => o.text === '1700'));
assert.ok(!elements.start.options.some(o => o.text === '0500'));
assert.equal(elements['example-start'].value, '480');
assert.equal(elements['example-start'].options[0].text, '0600');
assert.equal(elements['example-start'].options.at(-1).text, '2300');
elements['example-minutes'].value='15';
scope.updateExampleCheckpoint(1425);
assert.equal(elements['example-start'].value,'1425');
elements['example-minutes'].value='120';
scope.updateExampleCheckpoint();
assert.equal(elements['example-start'].value,'1320');
assert.ok(elements['example-start'].options.every(option=>Number(option.value)+120<=1440));

// Changing start beyond the previous end selects the first valid later time.
elements.start.value = '1020';
elements.start.onchange();
assert.equal(elements.end.value, '1035');
assert.ok(elements.end.options.every(o => Number(o.value) > 1020));
scope.setWindow(1425, 1440);
assert.equal(elements.end.options.length, 1);
assert.equal(elements.end.value, '1440');

// Loading saved profiles preserves valid endpoints, including midnight.
scope.setWindow(420, 1440);
assert.equal(elements.end.value, '1440');
scope.initializeWindowTimes(480, 600);
assert.equal(elements.start.options[0].text, '0800');
assert.equal(elements.end.options.at(-1).text, '1000');
assert.equal(elements.start.value, '480');
console.log('Time selector bounds, ordering, 24-hour labels and saved profiles passed.');
