import type { ExtensionManifest, ExtensionRoute } from './types.js';

// 内置扩展按需在这里注册。当前不展示示范性的“影像质量”页面。
const builtins: ExtensionManifest[] = [];

export class ExtensionRegistry {
  private readonly items = new Map<string, ExtensionManifest>();

  constructor(initial: ExtensionManifest[] = []) {
    initial.forEach((item) => this.register(item));
  }

  register(item: ExtensionManifest): void {
    if (this.items.has(item.id)) throw new Error(`扩展 ID 已注册：${item.id}`);
    if (!item.id || !item.version || !item.host_api_version) {
      throw new Error('扩展清单缺少 id、version 或 host_api_version');
    }
    this.items.set(item.id, item);
  }

  enabled(): ExtensionManifest[] {
    return [...this.items.values()].filter((item) => item.enabled);
  }

  routes(): ExtensionRoute[] {
    return this.enabled().flatMap((item) => item.routes ?? []);
  }

  navigation(): { label: string; to: string }[] {
    return this.enabled().flatMap((item) => item.nav ? [item.nav] : []);
  }
}

export const extensionRegistry = new ExtensionRegistry(builtins);
export const enabledExtensionRoutes = (): ExtensionRoute[] => extensionRegistry.routes();
export const enabledExtensionNavigation = (): { label: string; to: string }[] =>
  extensionRegistry.navigation();
