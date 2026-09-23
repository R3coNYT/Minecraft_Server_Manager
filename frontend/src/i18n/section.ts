/**
 * Une section du catalogue : les textes d'un écran, en anglais et en français
 * côte à côte. Le type exige que le français reprenne exactement les clés de
 * l'anglais — ni oubli, ni clé orpheline.
 */
export function section<const E extends Record<string, string>>(messages: {
  en: E
  fr: { [K in keyof E]: string }
}) {
  return messages
}
