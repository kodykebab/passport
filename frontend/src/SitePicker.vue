<script setup>
import { computed, ref } from "vue";
import { Alert, Button, FormControl } from "frappe-ui";
const { data } = defineProps({ data: Object });
const query = ref("");
const sites = computed(() =>
  (data.sites || []).filter((site) =>
    `${site.title} ${site.origin}`
      .toLowerCase()
      .includes(query.value.trim().toLowerCase())
  )
);
</script>
<template>
  <p class="text-sm text-ink-gray-6">
    Sites authorized through your Frappe ID. Each site still controls whether
    you can access it.
  </p>
  <FormControl label="Find a site" type="search" v-model="query" size="md" />
  <Alert
    v-if="!sites.length"
    theme="blue"
    :description="
      data.sites?.length
        ? 'No matching sites.'
        : 'No authorized sites yet. Start from a site that supports Frappe ID.'
    "
  />
  <section
    v-for="site in sites"
    :key="site.client"
    :aria-label="site.title"
    class="space-y-3 rounded-6 border border-outline-gray-1 bg-surface-base p-6"
  >
    <h2 class="break-words text-lg font-semibold">{{ site.title }}</h2>
    <p class="break-all text-sm text-ink-gray-6">{{ site.origin }}</p>
    <Button
      v-if="site.login_url"
      :href="site.login_url"
      variant="solid"
      size="md"
      >Open site</Button
    >
    <Alert
      v-else
      theme="blue"
      :description="
        site.revoked
          ? 'Access revoked. Manage connected sites to allow future sign-ins.'
          : 'This site is currently unavailable.'
      "
    />
  </section>
  <Button href="/account" variant="ghost">Manage Frappe ID account</Button>
</template>
