import { reactive } from 'vue';
/** UI preferences only; survives history replacement/route changes, not a page reload. */
export const activityDisclosure = reactive(new Map<string, boolean>());
export function setActivityDisclosure(key: string, open: boolean) {
  activityDisclosure.set(key, open);
  if (activityDisclosure.size > 200) activityDisclosure.delete(activityDisclosure.keys().next().value!);
}
