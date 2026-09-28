"""Generación de la rutina semanal estándar (entrenamiento + sugerencias de comida)."""

DAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

DAY_NAME_TO_INDEX = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "miércoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "sábado": 5,
    "domingo": 6,
}

# División estándar de grupos musculares; Domingo es descanso por defecto.
DEFAULT_TRAINING_SPLIT = [
    "piernas y glúteos",
    "pecho y brazos (empuje)",
    "espalda y hombros (jalón)",
    "core y cardio ligero",
    "full body funcional",
    "movilidad y estiramiento activo",
    "DESCANSO",
]

REST_LABEL = "DESCANSO"

# Rotación de sugerencias de comida (genéricas y seguras, varían día a día).
FOOD_SUGGESTIONS = [
    "proteína magra + verduras + una porción de carbohidrato complejo (avena, arroz integral o camote)",
    "huevo o yogur con fruta y un puñado de nueces",
    "pescado o pollo a la plancha con ensalada variada",
    "legumbres (lentejas o garbanzos) con vegetales al vapor",
    "batido de proteína con fruta y avena",
    "ensalada completa con proteína magra y aguacate",
    "sopa de verduras con proteína magra (opción ligera)",
]


# Tips educativos breves para el mensaje de "Educación/Receta" de las 2pm (genéricos, seguros,
# rotan por día de la semana igual que la comida y el entrenamiento).
DAILY_TIPS = [
    "Tomar agua simple entre comidas ayuda a que tu cuerpo aproveche mejor los nutrientes del día.",
    "Dormir 7-8 horas es tan importante para tu progreso como la alimentación y el ejercicio.",
    "Masticar despacio mejora la digestión y ayuda a sentir saciedad a tiempo.",
    "Caminar 10 minutos después de comer puede ayudar a tu digestión y a tus niveles de energía.",
    "Reducir el azúcar añadido poco a poco es más sostenible que eliminarlo de golpe.",
    "Preparar tus comidas con anticipación hace mucho más fácil mantener la constancia.",
    "Un domingo de descanso también es parte del protocolo: tu cuerpo se repara mientras descansas.",
]

# Micro-metas sugeridas para el resumen semanal (rotación simple, no un motor dinámico).
MICRO_METAS = [
    "tomar tu suplemento 5 de 7 días en el horario que elegiste",
    "completar tu entrenamiento los días que no son de descanso",
    "tomar al menos 2 litros de agua al día",
    "mandar tu foto de evidencia al menos 3 veces esta semana",
    "dormir a la misma hora al menos 5 noches de la semana",
]


def parse_rest_days(text: str) -> set:
    """Extrae índices de día (0=Lunes..6=Domingo) mencionados después de 'DESCANSO'."""
    days = set()
    for name, index in DAY_NAME_TO_INDEX.items():
        if name in text.lower():
            days.add(index)
    return days


def build_weekly_plan(rest_days: set) -> list:
    """Devuelve 7 tuplas (day_of_week, food_suggestion, training_focus)."""
    plan = []
    for day_index in range(7):
        food = FOOD_SUGGESTIONS[day_index]
        if day_index in rest_days:
            training = REST_LABEL
        else:
            training = DEFAULT_TRAINING_SPLIT[day_index]
        plan.append((day_index, food, training))
    return plan
