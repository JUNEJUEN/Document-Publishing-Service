<script setup lang="ts">
import { ref } from 'vue'
import { errorMessage } from '../http'
import { login } from '../session'

const clientId = ref('')
const clientSecret = ref('')
const busy = ref(false)
const error = ref('')

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await login(clientId.value, clientSecret.value)
  } catch (e) {
    error.value = errorMessage(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="card login" @submit.prevent="submit">
    <h2>Log in</h2>
    <label>Client id <input v-model="clientId" autocomplete="username" required /></label>
    <label>Client secret <input v-model="clientSecret" type="password" autocomplete="current-password" required /></label>
    <button :disabled="busy">{{ busy ? 'Logging in…' : 'Log in' }}</button>
    <p v-if="error" class="error">{{ error }}</p>
  </form>
</template>
