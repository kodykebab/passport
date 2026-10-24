<script setup>
import { computed, onMounted } from "vue";
import { Alert, FrappeUIProvider } from "frappe-ui";
import PasswordLogin from "./PasswordLogin.vue";
import EmailLogin from "./EmailLogin.vue";
import AccountPage from "./AccountPage.vue";
import SessionRecovery from "./SessionRecovery.vue";
const { data } = defineProps({ data: Object });
onMounted(() =>
  document.querySelector("[data-error], [data-autofocus]")?.focus()
);
const title = computed(() =>
  ["email", "password"].includes(data.page)
    ? "Sign in to Frappe ID"
    : data.page === "account"
    ? "Frappe ID account"
    : data.title
);
</script>
<template>
  <FrappeUIProvider>
    <main
      class="min-h-screen bg-surface-gray-1 px-4 py-10 text-ink-gray-9 sm:py-16"
    >
      <div
        class="mx-auto space-y-6"
        :class="data.page === 'account' ? 'max-w-xl' : 'max-w-sm'"
      >
        <h1 class="text-center text-2xl font-semibold">{{ title }}</h1>
        <Alert
          v-if="data.error"
          theme="red"
          :description="data.error"
          tabindex="-1"
          data-error
        />
        <AccountPage v-if="data.page === 'account'" :data="data" />
        <div
          v-else
          class="space-y-6 rounded-6 border border-outline-gray-1 bg-surface-base p-6 shadow-sm"
        >
          <PasswordLogin
            v-if="data.page === 'password'"
            :destination="data.destination"
            :enabled="data.password_enabled"
            :email-url="data.email_url"
          />
          <EmailLogin v-else-if="data.page === 'email'" :data="data" />
          <SessionRecovery v-else :data="data" />
        </div>
      </div>
    </main>
  </FrappeUIProvider>
</template>
