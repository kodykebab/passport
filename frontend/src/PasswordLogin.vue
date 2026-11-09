<script setup>
import { nextTick, ref } from "vue";
import { Alert, Button, FormControl } from "frappe-ui";

const { destination, enabled, emailUrl, signupUrl } = defineProps({
  destination: String,
  enabled: Boolean,
  emailUrl: String,
  signupUrl: String,
});
const email = ref("");
const password = ref("");
const pending = ref(false);
const error = ref("");
const errorNotice = ref(null);
// The native challenge lives only in this page; Frappe owns expiry and verification.
const challenge = ref(null);
const code = ref("");
const codeControl = ref(null);

function restart() {
  if (pending.value) return;
  challenge.value = null;
  code.value = "";
  error.value = "";
}

async function login() {
  if (pending.value) return;
  const verifying = !!challenge.value;
  pending.value = true;
  error.value = "";
  try {
    const response = await fetch("/api/method/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Accept: "application/json",
      },
      body: new URLSearchParams(
        verifying
          ? { otp: code.value.trim(), tmp_id: challenge.value.id }
          : { usr: email.value.trim(), pwd: password.value }
      ),
    });
    const result = await response.json();
    if (response.ok && ["Logged In", "No App"].includes(result.message)) {
      window.location.assign(destination);
      return;
    }
    if (response.ok && result.verification) {
      const verification = result.verification;
      const hasEnrollmentDetails =
        verification.method === "OTP App" &&
        typeof verification.qrcode === "string" &&
        verification.qrcode.length > 0 &&
        typeof verification.otp_secret === "string" &&
        verification.otp_secret.length > 0;
      if (
        typeof result.tmp_id !== "string" ||
        !result.tmp_id ||
        !["OTP App", "Email", "SMS"].includes(verification.method)
      ) {
        error.value =
          "This verification method is unavailable. Contact your administrator.";
      } else if (
        (!verification.setup && !hasEnrollmentDetails) ||
        verification.token_delivery === false
      ) {
        error.value =
          verification.method === "OTP App"
            ? "Authenticator setup is incomplete. Contact your administrator."
            : "The verification code could not be sent. Try signing in again or contact your administrator.";
      } else {
        let prompt = "Enter the verification code sent to you.";
        if (hasEnrollmentDetails) {
          prompt =
            "Scan this QR code with your authenticator app, then enter the code it displays.";
        } else if (
          typeof verification.prompt === "string" &&
          verification.prompt
        ) {
          prompt = verification.prompt;
        } else if (verification.method === "OTP App") {
          prompt = "Enter the verification code from your authenticator app.";
        }
        challenge.value = {
          id: result.tmp_id,
          qrcode: hasEnrollmentDetails ? verification.qrcode : null,
          secret: hasEnrollmentDetails ? verification.otp_secret : null,
          prompt,
        };
        await nextTick();
        codeControl.value?.$el.querySelector("input")?.focus();
      }
    } else if (verifying && result.exc_type === "ExpiredLoginException") {
      challenge.value = null;
      error.value =
        "Your sign-in attempt expired. Enter your password to start again.";
    } else if (
      response.status === 429 ||
      result.exc_type === "SecurityException"
    ) {
      error.value = verifying
        ? "Too many verification attempts. Please wait before trying again."
        : "Too many password attempts. Please wait before trying again.";
    } else if (response.status === 401) {
      error.value = verifying
        ? "The code is incorrect or your sign-in attempt expired. Try again or start over."
        : "Incorrect email or password. Please try again.";
    } else if (response.ok) {
      error.value =
        "Your account requires an additional sign-in step. Contact your administrator.";
    } else {
      error.value =
        "Sign-in is unavailable or not permitted. Please try again later or contact your administrator.";
    }
  } catch {
    error.value =
      "Unable to reach Frappe ID. Check your connection and try again.";
  } finally {
    password.value = "";
    code.value = "";
    pending.value = false;
    if (error.value) {
      await nextTick();
      errorNotice.value?.$el.focus();
    }
  }
}
</script>

<template>
  <Alert
    v-if="error"
    ref="errorNotice"
    theme="red"
    :description="error"
    tabindex="-1"
  />
  <form
    v-if="enabled"
    class="space-y-4"
    :aria-busy="pending"
    @submit.prevent="login"
  >
    <p v-if="challenge" class="text-sm text-gray-700">{{ challenge.prompt }}</p>
    <div v-if="challenge?.qrcode" class="space-y-3">
      <img
        :src="'data:image/svg+xml;base64,' + challenge.qrcode"
        alt="QR code to scan with your authenticator app"
        class="mx-auto w-48 max-w-full"
      />
      <p class="text-sm text-gray-700">Or enter this setup key manually:</p>
      <code class="block break-all text-sm" dir="ltr">{{
        challenge.secret
      }}</code>
    </div>
    <FormControl
      v-if="challenge"
      ref="codeControl"
      v-model="code"
      label="Verification code"
      type="text"
      name="otp"
      autocomplete="one-time-code"
      inputmode="numeric"
      required
      size="md"
    />
    <FormControl
      v-if="!challenge"
      v-model="email"
      label="Email"
      type="email"
      name="usr"
      autocomplete="username"
      required
      data-autofocus
      size="md"
    />
    <FormControl
      v-if="!challenge"
      v-model="password"
      label="Password"
      type="password"
      name="pwd"
      autocomplete="current-password"
      required
      size="md"
    />
    <Button
      type="submit"
      variant="solid"
      size="md"
      class="w-full"
      :loading="pending"
      :loading-text="challenge ? 'Verifying…' : 'Signing in…'"
      >{{ challenge ? "Verify" : "Sign in" }}</Button
    >
    <Button
      v-if="challenge"
      type="button"
      variant="ghost"
      class="w-full"
      :disabled="pending"
      @click="restart"
      >Start over</Button
    >
  </form>
  <Alert
    v-else
    theme="amber"
    description="Password login is disabled for this site."
  />
  <Button
    v-if="emailUrl && !challenge"
    :href="emailUrl"
    target="_self"
    variant="outline"
    size="md"
    class="w-full"
    >Use email code</Button
  >
  <Button
    v-if="signupUrl && !challenge"
    :href="signupUrl"
    target="_self"
    variant="ghost"
    size="md"
    class="w-full"
    >Create a Frappe ID</Button
  >
</template>
