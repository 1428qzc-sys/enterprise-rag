import { ref } from "vue";

const message = ref("");
let timer: ReturnType<typeof setTimeout> | undefined;

export function useToast() {
  function show(msg: string, ms = 2600) {
    message.value = msg;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => (message.value = ""), ms);
  }
  return { message, show };
}
