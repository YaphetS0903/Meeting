<script setup lang="ts">
import { useRoute } from "vue-router";
defineProps<{ links: readonly (readonly any[])[] }>();
defineEmits<{ navigate: [] }>();
const route = useRoute();
</script>

<template>
  <nav class="workspace-navigation" aria-label="主导航">
    <RouterLink
      v-for="[path, label, icon] in links"
      :key="path"
      :to="path"
      :class="{
        'router-link-active':
          path === '/settings' && route.path.startsWith('/settings/'),
      }"
      :aria-label="label"
      :title="label"
      @click="$emit('navigate')"
    >
      <el-icon><component :is="icon" /></el-icon><span>{{ label }}</span>
    </RouterLink>
  </nav>
</template>
