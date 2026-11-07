<template>
  <div class="prompt-chips">
    <span class="pc-label">试试：</span>
    <el-tag
      v-for="p in sugs" :key="p" class="pc-chip" size="small"
      effect="plain" type="primary" @click="$emit('pick', p)"
    >{{ p }}</el-tag>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { suggestionsFor } from '../ai'

const props = defineProps({ page: { type: String, default: '' } })
defineEmits(['pick'])

const route = useRoute()
const sugs = computed(() => suggestionsFor(props.page || route.path))
</script>

<style scoped>
.prompt-chips {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
  margin-bottom: 8px;
}
.pc-label {
  font-size: 12px;
  color: var(--dp-text-3);
  flex-shrink: 0;
}
.pc-chip {
  cursor: pointer;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pc-chip:hover {
  opacity: 0.75;
  border-color: var(--dp-accent);
}
</style>
