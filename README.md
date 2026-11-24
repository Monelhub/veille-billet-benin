# Veille vols spéciaux Paris – Cotonou

Ce robot surveille **voyage.benin.bj** (le site officiel de Bénin Tours pour les vols affrétés), **gouv.bj**, **benin.bj** et **Google Actualités**. Il vérifie environ toutes les 45 secondes, sauf quand GitHub prend du retard. Il t'alerte sur ton téléphone dès qu'un de ces sites change.

| Ce qui se passe | Niveau d'alerte |
|---|---|
| « Bientôt disponible » disparaît, ou des mots comme « réserver », « € », « décembre » apparaissent, ou le site redirige ailleurs | 🚨 **URGENT**, envoyé 3 fois |
| Le texte du site officiel change autrement | ⚠️ Important |
| Le site officiel ne répond plus (panne ou afflux de visiteurs) | ⚠️ Important |
| Un article de presse ou gouv.bj parle de vols vers Cotonou | ⚠️ Important |
| Le code du site est redéployé (le texte est le même, mais une mise en ligne se prépare peut-être) | ℹ️ Info |
| Chaque matin à 9 h : « je tourne toujours » | ℹ️ Info |

L'installation prend environ **15 minutes, à faire une seule fois**. Ensuite, tu n'as plus rien à faire.

---

## Étape 1 : notifications push sur le téléphone avec ntfy (2 min, recommandé)

1. Installe l'appli **ntfy** (Android ou iPhone).
2. Appuie sur « + » et abonne-toi à un sujet que tu inventes, long et difficile à deviner. Par exemple : `benin-vols-k7q2x9`.
3. Dans les réglages de ce sujet, autorise les notifications **prioritaires** à passer outre le mode « Ne pas déranger ».

Ce sujet est ton `NTFY_TOPIC`.

## Étape 2 : Telegram (3 min, recommandé)

1. Dans Telegram, écris à **@BotFather**, envoie `/newbot` et choisis un nom. Il te donne un **token** : c'est ton `TELEGRAM_TOKEN`.
2. Écris « bonjour » à ton nouveau bot.
3. Ouvre `https://api.telegram.org/bot<TON_TOKEN>/getUpdates` dans un navigateur et repère `"chat":{"id": 123456789`. Ce nombre est ton `TELEGRAM_CHAT_ID`.
   Pour alerter aussi un proche, ajoute son ID après une virgule : `123,456`.

## Étape 3 : email (facultatif)

Avec Gmail : va dans Compte Google → Sécurité → Validation en 2 étapes → **Mots de passe des applications**, puis crée-en un.

- `SMTP_HOST` = `smtp.gmail.com`
- `SMTP_PORT` = `465`
- `SMTP_USER` = ton adresse Gmail
- `SMTP_PASS` = le mot de passe d'application (16 lettres)
- `EMAIL_TO` = l'adresse où recevoir les alertes

## Étape 4 : mettre le robot sur GitHub (5 min)

1. Crée un compte sur github.com si tu n'en as pas.
2. Crée un nouveau dépôt **public** (le nom est libre, par exemple `veille-benin`). Il doit être public pour que l'exécution toutes les 5 minutes reste gratuite. Tes mots de passe ne seront pas visibles, car ils sont stockés à part dans les « Secrets ».
3. Clique sur **Add file → Upload files** et dépose `veille_benin.py` et `README.md`.
   Le fichier du workflow se trouve dans un dossier caché. Pour l'ajouter : **Add file → Create new file**, tape comme nom `.github/workflows/veille.yml`, puis colle le contenu du fichier `veille.yml`.
4. Va dans **Settings → Secrets and variables → Actions → New repository secret** et crée un secret pour chaque valeur obtenue aux étapes 1 à 3 : `NTFY_TOPIC`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`, et les `SMTP_*` et `EMAIL_TO` si tu utilises l'email.
5. Va dans l'onglet **Actions**. Si GitHub le demande, clique sur « I understand… enable ». Ensuite, ouvre « Veille vols Bénin », clique sur **Run workflow**, coche **test** et valide.
   👉 Tu dois recevoir une alerte de test sur chaque canal configuré. Si un canal ne reçoit rien, ouvre le journal de l'exécution pour voir l'erreur.
6. Lance-le une deuxième fois **sans** cocher test. Ce premier passage enregistre l'état actuel du site. Ensuite, le robot tourne tout seul toutes les 5 minutes.

## Plan B, totalement indépendant (3 min)

Crée un compte gratuit sur **UptimeRobot** et ajoute un moniteur de type **Keyword** :

- URL : `https://voyage.benin.bj/`
- Mot-clé : `Bientôt disponible`
- Condition : alerter quand le mot-clé **n'existe plus**
- Intervalle : 5 min

Active les alertes par email et dans leur appli mobile. Si GitHub a un problème, tu es quand même prévenu.

## Faire tourner le robot sur ton PC ou un serveur (au lieu de GitHub, ou en plus)

```bash
export NTFY_TOPIC=benin-vols-k7q2x9   # et/ou TELEGRAM_TOKEN, TELEGRAM_CHAT_ID…
python3 veille_benin.py --test          # vérifie que les alertes arrivent
python3 veille_benin.py --loop --interval 60
```

---

## ⚠️ Arnaques

En novembre 2026, la présidence du Bénin a signalé de **faux sites** qui imitent l'adresse de réservation. Ne paie **que** sur `https://voyage.benin.bj` : vérifie l'adresse exacte dans la barre du navigateur. Ne paie jamais via un lien reçu par WhatsApp ou par SMS.
