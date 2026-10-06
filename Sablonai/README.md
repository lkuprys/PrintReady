# Modelių Šablonai (Templates)

Į šį aplanką kelkite MacBook bei kitų produktų **.PNG** kontūrų šablonus.

### Pavyzdžiai:
- `A2681.png` (MacBook Air M2 13")
- `1932.png` (MacBook Air 13" Retina)
- `NEO.png`
- `A2442.png` (MacBook Pro 14")
- `A2485.png` (MacBook Pro 16")

### Reikalavimai šablonams:
1. **Formatas**: 300 DPI PNG su skaidriu (permatomu) fonu.
2. **Forma**: Balta arba spalvota produkto forma, išorė – 100% permatoma (Alpha = 0).
3. **Atpažinimas**: Programa automatiškai atpažįsta šabloną pagal failo pavadinimą arba pilną katalogo kelią.

## Šablonų taisyklės

Kiekvienam šablonui programoje (skiltis **Šablonai** → **Redaguoti**) galima nustatyti papildomas taisykles:

- **Spaudos failo pasukimas** (0°, 90°, 180°, 270°) – pasukamas visas failas: kontūras ir nuotrauka.
- **Nuotraukos pasukimas kontūre** – pasukama tik kliento nuotrauka, kontūras lieka vietoje.
- **Veidrodinis atspindys** – kairė ↔ dešinė.

Taisyklės saugomos šiame aplanke, faile `sablonu_nustatymai.json`, pvz.:

```json
{
  "2681": {"output_rotation": 180}
}
```

Programos atnaujinimas šio failo neperrašo. Taisyklės galioja naujai gaminamiems failams.
