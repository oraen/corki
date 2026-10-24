import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// The browser may be on a different OS than the manager host. Never choose
// the process command from navigator.platform or a browser user agent.
const source = fs.readFileSync(new URL('../static/tools.js', import.meta.url), 'utf8');
for (const python of ['C:\\Program Files\\Python\\python.exe', '/opt/runtime/bin/python']) {
  const context = vm.createContext({
    state: {runtime: {python}},
    document: {querySelector: () => ({addEventListener() {}})},
  });
  vm.runInContext(source, context);
  const demo = vm.runInContext('customToolDemo("process")', context);
  assert.equal(demo.command, python);
  assert.equal(demo.args[0], '-c');
  assert.equal(demo.parameters.type, 'object');
  assert.equal(demo.transport, 'process');
  // The interpreter path remains one executable argument, never shell quoted.
  assert.equal(demo.args.length, 2);
  assert.match(demo.args[1], /json.load\(sys.stdin\)/);
  assert.equal(vm.runInContext('customToolDemo("http").method', context), 'GET');
}
console.log('Custom tool examples use the manager host interpreter.');
