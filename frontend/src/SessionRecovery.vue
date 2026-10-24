<script setup>
import { Alert, Button } from "frappe-ui";
import PostForm from "./PostForm.vue";
defineProps({ data: Object });
</script>
<template>
  <Alert theme="blue" :description="data.message" />
  <PostForm
    v-if="data.csrf_token"
    action="/passport/retry"
    :fields="{ csrf_token: data.csrf_token, response_format: 'html' }"
    v-slot="{ pending }"
  >
    <Button
      type="submit"
      variant="solid"
      size="md"
      class="w-full"
      :loading="pending"
      loading-text="Validating…"
      >Retry validation</Button
    >
  </PostForm>
  <p class="text-center text-sm">
    <Button :href="data.link" target="_self" variant="ghost" size="md">{{
      data.link_label
    }}</Button>
  </p>
</template>
