import type { Component } from 'vue';

export interface ExtensionRoute {
  path: string;
  name: string;
  title: string;
  component: () => Promise<{ default: Component }>;
}

export interface ExtensionManifest {
  id: string;
  name: string;
  version: string;
  host_api_version: string;
  enabled: boolean;
  routes?: ExtensionRoute[];
  nav?: { label: string; to: string };
}
