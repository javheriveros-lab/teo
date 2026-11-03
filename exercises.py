"""Rutinas de entrenamiento por tipo (en casa, gimnasio, calistenia, yoga, tai chi) y nivel
(principiante, intermedio, avanzado). Solo nombres claros de ejercicios — sin imágenes: el
cliente busca cada nombre en YouTube para ver la ejecución correcta."""

TRAINING_TYPES = ("casa", "gimnasio", "calistenia", "yoga", "tai chi")
LEVELS = ("principiante", "intermedio", "avanzado")

TYPE_LABELS = {
    "casa": "En casa con peso corporal",
    "gimnasio": "Gimnasio",
    "calistenia": "Calistenia",
    "yoga": "Yoga",
    "tai chi": "Tai chi",
}

YOUTUBE_NOTE = "Busca el nombre de cada ejercicio en YouTube para ver su ejecución correcta."

# Cada tipo: 6 sesiones (Lunes a Sábado; Domingo es descanso por defecto). Cada sesión es
# (enfoque, [ejercicios]) y cada ejercicio es un nombre fijo o una tupla con su variante para
# (principiante, intermedio, avanzado).
_MOBILITY_SESSION = (
    "movilidad y estiramiento activo",
    [
        "Gato-vaca",
        "Estiramiento de flexor de cadera en zancada",
        "Rotación torácica en cuatro puntos",
        ("Estiramiento de isquiotibiales de pie", "Estiramiento del mejor del mundo (world's greatest stretch)",
         "Sentadilla profunda sostenida (deep squat hold)"),
    ],
)

ROUTINES = {
    "casa": [
        ("piernas y glúteos", [
            ("Sentadilla con silla", "Sentadilla libre", "Sentadilla con salto"),
            ("Puente de glúteos", "Puente de glúteos a una pierna", "Hip thrust a una pierna con pausa"),
            ("Zancada estática", "Zancada inversa alterna", "Sentadilla búlgara"),
            ("Elevación de talones", "Elevación de talones a una pierna", "Elevación de talones a una pierna con pausa"),
        ]),
        ("pecho y brazos (empuje)", [
            ("Flexiones en pared", "Flexiones de rodillas", "Flexiones de pecho"),
            ("Flexiones inclinadas en mesa", "Flexiones de pecho", "Flexiones declinadas"),
            ("Fondos de tríceps en silla con rodillas dobladas", "Fondos de tríceps en silla",
             "Fondos de tríceps en silla con piernas extendidas"),
            ("Elevaciones laterales con botellas de agua", "Flexiones de pica", "Flexiones de pica con pies elevados"),
        ]),
        ("espalda y hombros (jalón)", [
            ("Remo con toalla en puerta", "Remo invertido bajo mesa", "Remo invertido bajo mesa con pies elevados"),
            ("Superman", "Superman Y-T-W", "Superman con pausa"),
            ("Bird dog", "Bird dog con pausa", "Plancha con toque de hombro"),
            ("Ángeles en el piso", "Remo con mochila", "Remo a una mano con mochila"),
        ]),
        ("core y cardio", [
            ("Plancha frontal sobre rodillas", "Plancha frontal", "Plancha con elevación de pierna"),
            ("Dead bug", "Bicicleta abdominal", "Escaladores (mountain climbers)"),
            ("Marcha en el lugar", "Rodillas elevadas (high knees)", "Burpees"),
            ("Plancha lateral sobre rodillas", "Plancha lateral", "Plancha lateral con elevación de cadera"),
        ]),
        ("full body funcional", [
            ("Jumping jacks sin salto", "Jumping jacks", "Burpees"),
            ("Sentadilla con alcance de brazos", "Zancada con giro de torso", "Zancada con salto"),
            ("Flexiones en pared", "Flexiones de rodillas", "Flexión con rotación en T"),
            ("Oso estático (bear hold)", "Oso caminando (bear crawl)", "Oso caminando con toque de hombro"),
        ]),
        _MOBILITY_SESSION,
    ],
    "gimnasio": [
        ("piernas y glúteos", [
            ("Prensa de piernas", "Sentadilla con barra", "Sentadilla con barra"),
            ("Curl femoral en máquina", "Peso muerto rumano con mancuernas", "Peso muerto rumano con barra"),
            ("Extensión de cuádriceps en máquina", "Zancadas con mancuernas", "Sentadilla búlgara con mancuernas"),
            ("Elevación de talones en máquina", "Hip thrust en máquina", "Hip thrust con barra"),
        ]),
        ("pecho y brazos (empuje)", [
            ("Press de pecho en máquina", "Press de banca con mancuernas", "Press de banca con barra"),
            ("Press de hombro en máquina", "Press militar con mancuernas", "Press militar con barra de pie"),
            ("Aperturas en máquina (pec deck)", "Press inclinado con mancuernas", "Fondos en paralelas"),
            ("Extensión de tríceps en polea", "Extensión de tríceps en polea", "Press francés con barra Z"),
        ]),
        ("espalda y hombros (jalón)", [
            ("Jalón al pecho en polea", "Jalón al pecho en polea", "Dominadas"),
            ("Remo sentado en polea", "Remo con mancuerna a una mano", "Remo con barra"),
            "Face pull en polea",
            ("Curl de bíceps con mancuernas", "Curl de bíceps con barra", "Curl martillo con mancuernas"),
        ]),
        ("core y cardio", [
            ("Caminadora inclinada", "Bicicleta estática por intervalos", "Remo ergómetro por intervalos"),
            ("Plancha frontal", "Crunch en polea alta", "Rueda abdominal (ab wheel)"),
            "Pallof press en polea",
            ("Elevación de rodillas en silla romana", "Elevación de piernas en silla romana",
             "Elevación de piernas colgado en barra"),
        ]),
        ("full body funcional", [
            ("Peso muerto sumo con mancuerna", "Peso muerto con barra", "Peso muerto con barra"),
            ("Press de pecho en máquina", "Press inclinado con mancuernas", "Push press con barra"),
            ("Remo en máquina", "Jalón al pecho agarre neutro", "Dominadas agarre neutro"),
            "Paseo del granjero (farmer walk)",
        ]),
        ("movilidad y estiramiento activo", [
            "Bicicleta estática suave",
            "Liberación miofascial con foam roller",
            "Estiramiento de flexor de cadera en zancada",
            "Rotación torácica en cuatro puntos",
        ]),
    ],
    "calistenia": [
        ("piernas y glúteos", [
            ("Sentadilla libre", "Sentadilla búlgara", "Sentadilla pistol asistida"),
            ("Zancada inversa", "Zancada con salto", "Sentadilla pistol"),
            ("Puente de glúteos", "Puente de glúteos a una pierna", "Curl nórdico asistido"),
            ("Elevación de talones", "Elevación de talones a una pierna", "Saltos al cajón (box jumps)"),
        ]),
        ("empuje", [
            ("Flexiones de rodillas", "Flexiones de pecho", "Flexiones arqueras (archer push-ups)"),
            ("Fondos en banco", "Fondos en paralelas", "Fondos en paralelas con pausa"),
            ("Flexiones de pica", "Flexiones de pica con pies elevados", "Pino asistido en pared (wall handstand)"),
            ("Plancha alta", "Flexiones diamante", "Flexiones pseudo planche"),
        ]),
        ("jalón", [
            ("Colgarse de la barra (dead hang)", "Dominadas negativas", "Dominadas"),
            ("Remo australiano", "Remo australiano con pies elevados", "Dominadas supinas (chin-ups)"),
            ("Retracción escapular en barra", "Retracción escapular en barra", "Dominadas arqueras asistidas"),
            ("Superman", "Elevación de rodillas colgado", "Elevación de piernas a la barra (toes to bar)"),
        ]),
        ("core y cardio", [
            ("Hollow body con rodillas dobladas", "Hollow body hold", "Hollow body rocks"),
            ("Plancha frontal", "L-sit con rodillas dobladas en paralelas", "L-sit en paralelas"),
            ("Escaladores (mountain climbers)", "Burpees", "Burpees con salto al pecho"),
            ("Plancha lateral", "Plancha lateral con elevación de cadera", "Dragon flag negativa"),
        ]),
        ("full body funcional", [
            ("Burpees sin salto", "Burpees", "Burpees con dominada"),
            ("Sentadilla libre", "Sentadilla con salto", "Sentadilla pistol"),
            ("Flexiones de rodillas", "Flexiones de pecho", "Flexiones explosivas (clap push-ups)"),
            ("Remo australiano", "Dominadas negativas", "Muscle-up asistido con banda"),
        ]),
        _MOBILITY_SESSION,
    ],
    "yoga": [
        ("fuerza de piernas y equilibrio", [
            ("Postura de la montaña (Tadasana)", "Guerrero I (Virabhadrasana I)", "Guerrero III (Virabhadrasana III)"),
            ("Guerrero II (Virabhadrasana II)", "Postura de la silla (Utkatasana)", "Media luna (Ardha Chandrasana)"),
            ("Postura del árbol (Vrksasana)", "Triángulo (Trikonasana)", "Triángulo en torsión (Parivrtta Trikonasana)"),
            ("Postura del niño (Balasana)", "Puente (Setu Bandhasana)", "Rueda (Urdhva Dhanurasana)"),
        ]),
        ("brazos y hombros", [
            "Perro boca abajo (Adho Mukha Svanasana)",
            ("Plancha con rodillas apoyadas", "Plancha (Phalakasana)", "Chaturanga (Chaturanga Dandasana)"),
            ("Cobra (Bhujangasana)", "Perro boca arriba (Urdhva Mukha Svanasana)", "Cuervo (Bakasana)"),
            ("Postura del delfín", "Plancha lateral (Vasisthasana)", "Pino de antebrazos asistido (Pincha Mayurasana)"),
        ]),
        ("espalda y apertura de pecho", [
            "Gato-vaca (Marjaryasana-Bitilasana)",
            ("Esfinge (Salamba Bhujangasana)", "Langosta (Salabhasana)", "Arco (Dhanurasana)"),
            ("Torsión sentada fácil", "Torsión sentada (Ardha Matsyendrasana)", "Camello (Ustrasana)"),
            ("Postura del niño (Balasana)", "Hilo en la aguja (Parsva Balasana)", "Paloma (Eka Pada Rajakapotasana)"),
        ]),
        ("core y equilibrio", [
            ("Saludo al sol A (Surya Namaskar A)", "Saludo al sol A (Surya Namaskar A)", "Saludo al sol B (Surya Namaskar B)"),
            ("Barco con rodillas dobladas", "Barco (Navasana)", "Barco con brazos sobre la cabeza"),
            ("Postura del árbol (Vrksasana)", "Guerrero III (Virabhadrasana III)", "Postura del bailarín (Natarajasana)"),
            ("Plancha (Phalakasana)", "Plancha lateral (Vasisthasana)", "Cuervo (Bakasana)"),
        ]),
        ("flujo completo", [
            ("Saludo al sol A (Surya Namaskar A)", "Saludo al sol B (Surya Namaskar B)", "Saludo a la luna (Chandra Namaskar)"),
            ("Guerrero II (Virabhadrasana II)", "Ángulo lateral extendido (Utthita Parsvakonasana)",
             "Ángulo lateral en torsión (Parivrtta Parsvakonasana)"),
            ("Pinza de pie (Uttanasana)", "Pinza de pie (Uttanasana)", "Pinza con piernas abiertas (Prasarita Padottanasana)"),
            ("Puente (Setu Bandhasana)", "Puente (Setu Bandhasana)", "Rueda (Urdhva Dhanurasana)"),
        ]),
        ("restaurativo", [
            "Postura del niño (Balasana)",
            "Piernas en la pared (Viparita Karani)",
            "Mariposa reclinada (Supta Baddha Konasana)",
            "Relajación final (Savasana)",
        ]),
    ],
    "tai chi": [
        ("postura y respiración", [
            "Respiración abdominal de tai chi",
            ("Zhan Zhuang (postura del árbol)", "Zhan Zhuang (postura del árbol)", "Zhan Zhuang en postura baja"),
            "Ocho brocados (Ba Duan Jin)",
            "Separar la crin del caballo salvaje",
        ]),
        ("pasos y equilibrio", [
            "Paso del gato (tai chi)",
            "La grulla blanca extiende sus alas",
            "Rechazar al mono",
            "Gallo dorado sobre una pierna",
        ]),
        ("brazos y fluidez", [
            "Manos de nubes (Yun Shou)",
            "Agarrar la cola del gorrión",
            "Látigo simple (tai chi)",
            "Cepillar la rodilla y empujar",
        ]),
        ("forma completa", [
            "Ocho brocados (Ba Duan Jin)",
            ("Tai chi forma de 8 movimientos", "Tai chi forma de 24 movimientos (estilo Yang)",
             "Tai chi forma de 42 movimientos"),
            "Respiración abdominal de tai chi",
        ]),
        ("integración y repaso", [
            ("Zhan Zhuang (postura del árbol)", "Zhan Zhuang (postura del árbol)", "Zhan Zhuang en postura baja"),
            ("Tai chi forma de 8 movimientos", "Tai chi forma de 24 movimientos (estilo Yang)",
             "Tai chi forma de 42 movimientos"),
            ("Manos de nubes (Yun Shou)", "Empuje de manos en solitario (tui shou)", "Empuje de manos en solitario (tui shou)"),
        ]),
        ("qigong restaurativo", [
            "Ocho brocados (Ba Duan Jin)",
            "Qigong de las seis respiraciones curativas (Liu Zi Jue)",
            "Automasaje de qigong",
            "Meditación de pie en quietud",
        ]),
    ],
}

# Volumen por nivel: los tipos de fuerza se prescriben en series/repeticiones y los de práctica
# (yoga, tai chi) en respiraciones/minutos.
_STRENGTH_DOSE = {
    "principiante": "2 series de 10-12 repeticiones (o 20 segundos en ejercicios de tiempo), descanso de 60-90 segundos; sesión de 20-25 minutos",
    "intermedio": "3 series de 12-15 repeticiones (o 30-40 segundos en ejercicios de tiempo), descanso de 45-60 segundos; sesión de 30-40 minutos",
    "avanzado": "4 series de 10-15 repeticiones, dejando 1-2 repeticiones en reserva (o 45-60 segundos en ejercicios de tiempo), descanso de 45-60 segundos; sesión de 45-50 minutos",
}
DOSE_BY_TYPE = {
    "casa": _STRENGTH_DOSE,
    "calistenia": _STRENGTH_DOSE,
    "gimnasio": {
        "principiante": "2-3 series de 12 repeticiones con carga ligera y técnica impecable, descanso de 90 segundos; cardio 10-15 minutos suave; sesión de 30-40 minutos",
        "intermedio": "3-4 series de 8-12 repeticiones con carga moderada, descanso de 60-90 segundos; cardio por intervalos 15 minutos; sesión de 45-60 minutos",
        "avanzado": "4 series de 6-10 repeticiones con carga desafiante (1-2 repeticiones en reserva), descanso de 90-120 segundos en básicos; sesión de 60 minutos",
    },
    "yoga": {
        "principiante": "mantén cada postura 3-5 respiraciones y repite cada lado 2 veces; sesión de 20 minutos",
        "intermedio": "mantén cada postura 5-8 respiraciones y enlázalas con respiración (vinyasa); sesión de 35 minutos",
        "avanzado": "mantén cada postura 8-10 respiraciones en flujo continuo; sesión de 50 minutos",
    },
    "tai chi": {
        "principiante": "practica cada movimiento 3-4 minutos, lento y con respiración abdominal; sesión de 20 minutos",
        "intermedio": "practica cada movimiento 5 minutos y la forma completa 2 veces; sesión de 30 minutos",
        "avanzado": "practica cada movimiento 6-8 minutos en postura más baja y la forma completa 3 veces; sesión de 45 minutos",
    },
}


def _variant(exercise, level_index: int) -> str:
    return exercise if isinstance(exercise, str) else exercise[level_index]


def get_session(tipo: str, nivel: str, session_index: int):
    """(enfoque, [nombres de ejercicios]) de la sesión session_index (0-5) para ese tipo y nivel."""
    focus, exercises = ROUTINES[tipo][session_index]
    level_index = LEVELS.index(nivel)
    return focus, [_variant(e, level_index) for e in exercises]


def routine_summary(tipo: str, nivel: str, training_days: list) -> str:
    """Rutina completa en texto (enfoque, ejercicios y volumen) para que TEO se la explique al
    cliente. training_days: lista de (nombre_del_día, session_index)."""
    lines = [f"Rutina {TYPE_LABELS[tipo]} — nivel {nivel}. Volumen: {DOSE_BY_TYPE[tipo][nivel]}."]
    for day_name, session_index in training_days:
        focus, names = get_session(tipo, nivel, session_index)
        lines.append(f"- {day_name} ({focus}): {', '.join(names)}")
    lines.append(YOUTUBE_NOTE)
    return "\n".join(lines)
