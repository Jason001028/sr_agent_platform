import { describe, expect, it } from 'vitest';
import { ExtensionRegistry } from '../../extensions/registry.js';

const item = {
  id: 'demo', name: 'Demo', version: '1.0.0', host_api_version: '1', enabled: true,
};

describe('ExtensionRegistry', () => {
  it('rejects duplicate ids and exposes enabled extensions', () => {
    const registry = new ExtensionRegistry([item]);
    expect(() => registry.register(item)).toThrow('扩展 ID 已注册');
    expect(registry.enabled()).toHaveLength(1);
  });

  it('filters disabled extensions', () => {
    const registry = new ExtensionRegistry([{ ...item, id: 'off', enabled: false }]);
    expect(registry.enabled()).toHaveLength(0);
    expect(registry.routes()).toEqual([]);
  });
});
