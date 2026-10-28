<template>
  <div class="file-item">
    <slot name="thumbnail" />
    <slot />
    <div class="card-body"><slot name="body" /></div>
  </div>
</template>
<style scoped>
.card-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
</style>
