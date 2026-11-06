import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../static/channels.js',import.meta.url),'utf8');
const from=source.indexOf('  function lastChat(value)');
const until=source.indexOf('  function renderApplications()',from);
const now=1800000000;
const format=vm.runInNewContext('('+source.slice(from,until).trim()+')',{
 Date:class extends Date {static now(){return now*1000;}},
});
test('last conversation uses the requested relative units and six-month cap',()=>{
 for(const [age,expected] of [[20,'刚刚'],[61,'1分钟前'],[3600,'1小时前'],[10800,'3小时前'],
 [86400,'1天前'],[172800,'2天前'],[14*86400,'2周前'],[30*86400,'1个月前'],
 [180*86400,'半年前'],[365*86400,'半年前']]) assert.equal(format(now-age),expected);
 assert.equal(format(null),'—');assert.equal(format(NaN),'—');
});
