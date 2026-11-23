<script setup lang="ts">
import { Promotion } from "@element-plus/icons-vue";
import { state } from "../api";
import IssuedTaskRecords from "../components/IssuedTaskRecords.vue";
</script>
<template>
  <div class="issued-page">
    <section class="page-heading issued-heading">
      <div><h1>我派发的任务</h1></div>
      <RouterLink v-if="state.me?.can_assign_tasks" to="/assignments"
        ><el-button type="primary" :icon="Promotion"
          >直接派发任务</el-button
        ></RouterLink
      >
    </section>
    <IssuedTaskRecords class="panel" />
  </div>
</template>
<style scoped>
.issued-page {
  max-width: 1480px;
  margin: 0 auto;
}
.issued-heading {
  align-items: center;
  gap: 16px;
}
.issued-heading h1 {
  font-size: 26px;
  margin: 0;
}
@media (max-width: 680px) {
  .issued-heading {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .issued-heading h1 {
    font-size: 23px;
  }
}
</style>
