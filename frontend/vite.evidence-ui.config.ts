import { mergeConfig } from 'vite';
import base from './vite.config';
export default mergeConfig(base,{build:{outDir:'../work/evidence-ui-preview',rollupOptions:{input:'tests/evidence-ui-preview.html'}}});

