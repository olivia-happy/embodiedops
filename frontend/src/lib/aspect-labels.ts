const ASPECT_LABELS: Record<string, string> = {
  app: "应用",
  delivery: "配送",
  environment: "环境",
  food: "餐饮",
  price: "价格",
  service: "服务",
  unknown: "未分类",
};

export function formatAspectLabel(aspect: string): string {
  const value = aspect.trim();
  if (!value) return "未分类";
  return ASPECT_LABELS[value.toLowerCase()] ?? value;
}

export function formatOpportunityTitle(title: string, aspect: string): string {
  const rawAspect = aspect.trim();
  if (rawAspect && title.trim().toLowerCase() === `${rawAspect.toLowerCase()} 体验机会`) {
    return `${formatAspectLabel(aspect)}体验机会`;
  }
  return title;
}
