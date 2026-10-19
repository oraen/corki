/* Shared model-name matching for cards and conversation avatars. */
'use strict';
window.CorkiModelAvatar = (() => {
 const brands = [
  ['qwen', /qwen/i],
  ['deepseek', /deepseek/i],
  ['kimi', /kimi|moonshot/i],
  ['claude', /claude|anthropic/i],
  ['gemini', /gemini/i],
  ['glm', /glm/i],
  ['minimax', /minimax/i],
  ['xai', /grok|xai|x\.ai/i],
  ['mistral', /mistral|mixtral/i],
  ['openai', /openai|gpt|(?:^|[\s/_.-])o[134](?:$|[\s/_.-])/i],
 ];
 function id(model) {
  return typeof model === 'string' ? brands.find(([,pattern]) => pattern.test(model))?.[0] || '' : '';
 }
 function image(model, fallback = '') {
  const brand = id(model);
  return brand ? `<img class="model-brand-avatar" src="/provider-icons/${brand}.png" alt="" draggable="false">` : fallback;
 }
 return Object.freeze({id, image});
})();
