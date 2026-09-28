"""Catálogo curado de ejercicios (con peso corporal, seguros para principiantes) por enfoque
del día, con imágenes reales de wger.de (CC-BY-SA / licencia abierta) y técnica en español."""

# Cada entrada: (nombre, url_de_imagen, técnica_breve)
EXERCISES_BY_FOCUS = {
    "piernas y glúteos": [
        (
            "Puente de glúteos",
            "https://wger.de/media/exercise-images/265/7528acb4-b2cc-4b75-b6ae-d514cbd4f78b.png",
            "Acuéstate boca arriba con rodillas dobladas y pies apoyados. Eleva la cadera apretando "
            "los glúteos, mantén 1-2 segundos arriba y baja de forma controlada.",
        ),
        (
            "Arremetidas inversas",
            "https://wger.de/media/exercise-images/999/d0931eb3-8db0-4049-bb08-aa4036072056.jfif",
            "Da un paso hacia atrás y baja la rodilla trasera casi al piso manteniendo el torso "
            "recto. Empuja con el talón delantero para volver a la posición inicial.",
        ),
    ],
    "pecho y brazos (empuje)": [
        (
            "Flexiones de pecho (push-up)",
            "https://wger.de/media/exercise-images/1551/a6a9e561-3965-45c6-9f2b-ee671e1a3a45.png",
            "Manos un poco más anchas que los hombros, cuerpo recto de cabeza a talones. Baja el "
            "pecho cerca del piso y empuja de vuelta sin arquear la espalda baja.",
        ),
        (
            "Flexiones de pica (pike push-up)",
            "https://wger.de/media/exercise-images/454/447f3c17-405f-46e0-b138-65c2a8caaab0.png",
            "En posición de V invertida con la cadera elevada, baja la cabeza hacia el piso "
            "doblando los codos y empuja de vuelta enfocando los hombros.",
        ),
    ],
    "espalda y hombros (jalón)": [
        (
            "Bird dog",
            "https://wger.de/media/exercise-images/1572/3d14e761-a73d-49da-8804-f3016a7573ff.png",
            "En cuatro puntos de apoyo, extiende un brazo y la pierna opuesta manteniendo la "
            "espalda plana y el abdomen firme. Alterna de forma lenta y controlada.",
        ),
        (
            "Postura del niño",
            "https://wger.de/media/exercise-images/1002/ddf91765-8045-4087-bece-de17f33332ce.png",
            "Siéntate sobre los talones, estira los brazos hacia adelante y baja el pecho hacia el "
            "piso. Relaja la espalda baja y respira profundo.",
        ),
    ],
    "core y cardio ligero": [
        (
            "Plancha frontal",
            "https://wger.de/media/exercise-images/1307/321d796a-f394-40b8-a1ea-874bbed6ecee.png",
            "Antebrazos y puntas de los pies en el piso, cuerpo en línea recta de cabeza a talones, "
            "abdomen contraído, sin dejar caer ni elevar la cadera.",
        ),
        (
            "Rodillas elevadas (high knees)",
            "https://wger.de/media/exercise-images/983/16245344-9957-4a24-8d61-f9939ed5f964.png",
            "Trota en el mismo lugar llevando las rodillas a la altura de la cadera, a un ritmo "
            "que puedas controlar sin perder la postura.",
        ),
    ],
    "full body funcional": [
        (
            "Jumping jacks",
            "https://wger.de/media/exercise-images/320/6c9124b6-3551-47a8-9c22-20141c8b9c53.png",
            "Salta abriendo piernas y brazos al mismo tiempo y vuelve a la posición inicial, de "
            "forma rítmica y controlada.",
        ),
        (
            "Flexiones de pecho (push-up)",
            "https://wger.de/media/exercise-images/1551/a6a9e561-3965-45c6-9f2b-ee671e1a3a45.png",
            "Manos un poco más anchas que los hombros, cuerpo recto de cabeza a talones. Baja el "
            "pecho cerca del piso y empuja de vuelta sin arquear la espalda baja.",
        ),
    ],
    "movilidad y estiramiento activo": [
        (
            "Estiramiento de pantorrilla de pie",
            "https://wger.de/media/exercise-images/1239/5026373a-a7b4-4e26-a0aa-c46634205196.jpg",
            "Apoya las manos en una pared, un pie atrás con el talón en el piso, inclínate hacia "
            "adelante hasta sentir el estiramiento en la pantorrilla. Mantén 20-30 segundos.",
        ),
        (
            "Estiramiento de bíceps de pie",
            "https://wger.de/media/exercise-images/1232/2b6de046-5806-49e3-bf36-b6fae16af021.png",
            "Extiende el brazo hacia atrás con la palma hacia arriba contra una pared o superficie, "
            "gira suavemente el torso hacia el lado contrario hasta sentir el estiramiento.",
        ),
    ],
}


def get_exercises_for_focus(training_focus: str) -> list:
    """Devuelve la lista de (nombre, imagen, técnica) para el enfoque del día, o [] si es descanso."""
    return EXERCISES_BY_FOCUS.get((training_focus or "").lower(), [])
