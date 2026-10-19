const brands: [string, RegExp][] = [
  ["qwen", /qwen/i],
  ["deepseek", /deepseek/i],
  ["kimi", /kimi|moonshot/i],
  ["claude", /claude|anthropic/i],
  ["gemini", /gemini|google/i],
  ["glm", /glm|zhipu/i],
  ["minimax", /minimax/i],
  ["xai", /grok|xai|x\.ai/i],
  ["mistral", /mistral|mixtral/i],
  ["openai", /openai|gpt|(?:^|[\s/_.-])o[134](?:$|[\s/_.-])/i],
];
export function modelIcon(name: string): string | undefined {
  const brand = brands.find(([, pattern]) => pattern.test(name))?.[0];
  return brand ? `resources/provider-icons/${brand}.png` : undefined;
}
