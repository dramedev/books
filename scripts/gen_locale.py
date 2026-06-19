# -*- coding: utf-8 -*-
"""Generates locale/<lang>/LC_MESSAGES/django.po for fr and ar from a
hand-written translation table, then compiles them to .mo with msgfmt.py.

Run from the project root: venv/Scripts/python scripts/gen_locale.py
"""

import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HEADER = """msgid ""
msgstr ""
"Project-Id-Version: RumiPress\\n"
"Report-Msgid-Bugs-To: \\n"
"MIME-Version: 1.0\\n"
"Content-Type: text/plain; charset=UTF-8\\n"
"Content-Transfer-Encoding: 8bit\\n"
"Language: {lang}\\n"

"""

# ---------------------------------------------------------------------------
# French translations
# ---------------------------------------------------------------------------
FR = {
    # Navigation / layout
    "Dashboard": "Tableau de bord",
    "Catalog": "Catalogue",
    "Books": "Livres",
    "Stock": "Stock",
    "Categories": "Catégories",
    "Authors": "Auteurs",
    "Sales": "Ventes",
    "Reports": "Rapports",
    "Help": "Aide",
    "About": "À propos",
    "Logout": "Déconnexion",
    "Theme": "Thème",
    "Login": "Connexion",
    "Language": "Langue",
    "RumiPress Assistant": "Assistant RumiPress",
    "Ask a question...": "Posez une question...",
    "Toggle navigation": "Afficher/masquer la navigation",
    "Open AI assistant": "Ouvrir l'assistant IA",
    "Thinking...": "Réflexion en cours...",
    "Sorry, something went wrong.": "Désolé, une erreur est survenue.",

    # About page
    "About Rumi Press": "À propos de Rumi Press",
    "What each part of the system does.": "Ce que fait chaque partie du système.",
    "Profile": "Profil",
    "A quick overview of the whole catalog: total books, total units sold, low stock count, total revenue and profit, a revenue/profit-by-category chart, a sales trend chart, the books currently low on stock, the top sellers, and the most recent sales.":
        "Un aperçu rapide de l'ensemble du catalogue : nombre total de livres, total des unités vendues, nombre de stocks faibles, revenu et profit totaux, un graphique des revenus/profits par catégorie, un graphique de la tendance des ventes, les livres actuellement en stock faible, les meilleures ventes et les ventes les plus récentes.",
    "The catalog of titles: ISBN, title, subtitle, authors, publisher, publish date, category and distribution expense. Search, filter by category/publisher, sort, and export the list to CSV, Excel or PDF.":
        "Le catalogue des titres : ISBN, titre, sous-titre, auteurs, éditeur, date de publication, catégorie et frais de distribution. Recherchez, filtrez par catégorie/éditeur, triez et exportez la liste en CSV, Excel ou PDF.",
    "Stock on hand and reorder thresholds for every book. Books at or below their threshold are flagged \"Low stock\" and can be filtered to a low-stock-only view.":
        "Le stock disponible et les seuils de réapprovisionnement pour chaque livre. Les livres au niveau ou en dessous de leur seuil sont marqués « Stock faible » et peuvent être filtrés dans une vue dédiée.",
    "Groupings used to organize books for filtering and reporting. Each category shows how many books belong to it, and can be added, edited or deleted (categories still in use can't be deleted).":
        "Des regroupements utilisés pour organiser les livres pour le filtrage et les rapports. Chaque catégorie affiche le nombre de livres qui lui appartiennent, et peut être ajoutée, modifiée ou supprimée (les catégories encore utilisées ne peuvent pas être supprimées).",
    "The list of authors linked to books, with a count of how many books each author has. Authors can be added, edited or removed once they are no longer linked to any book.":
        "La liste des auteurs liés aux livres, avec le nombre de livres de chaque auteur. Les auteurs peuvent être ajoutés, modifiés ou supprimés une fois qu'ils ne sont plus liés à aucun livre.",
    "Every sale transaction: book, quantity, unit price, revenue, date and channel. Recording a sale reduces the book's stock on hand and shows a remaining-stock notification; a sale is refused if it would exceed available stock. The list can be exported to CSV, Excel or PDF.":
        "Chaque transaction de vente : livre, quantité, prix unitaire, revenu, date et canal. L'enregistrement d'une vente réduit le stock disponible du livre et affiche une notification de stock restant ; une vente est refusée si elle dépasse le stock disponible. La liste peut être exportée en CSV, Excel ou PDF.",
    "A filterable distribution report: total books, expense, revenue and profit, with charts for expenses and revenue/profit by category and a sales trend over time. Results can be exported to CSV, Excel or PDF.":
        "Un rapport de distribution filtrable : total des livres, dépenses, revenus et profits, avec des graphiques des dépenses et des revenus/profits par catégorie et une tendance des ventes au fil du temps. Les résultats peuvent être exportés en CSV, Excel ou PDF.",
    "Each user can upload a profile photo, shown in the sidebar. Access to each section above is controlled by permissions, so what you see depends on your assigned role.":
        "Chaque utilisateur peut télécharger une photo de profil, affichée dans la barre latérale. L'accès à chaque section ci-dessus est contrôlé par des permissions, donc ce que vous voyez dépend du rôle qui vous est attribué.",

    # Common
    "Edit": "Modifier",
    "Delete": "Supprimer",
    "Save": "Enregistrer",
    "Cancel": "Annuler",
    "Name": "Nom",
    "Actions": "Actions",
    "Add Author": "Ajouter un auteur",
    "Add Category": "Ajouter une catégorie",
    "Add Book": "Ajouter un livre",
    "Add Sale": "Ajouter une vente",
    "No authors yet.": "Aucun auteur pour le moment.",
    "No categories yet.": "Aucune catégorie pour le moment.",
    "Manage the authors linked to your books.": "Gérez les auteurs liés à vos livres.",
    "Manage the catalog, filter records, and open reports.": "Gérez le catalogue, filtrez les enregistrements et ouvrez les rapports.",
    "Group books for reporting and filtering.": "Regroupez les livres pour les rapports et le filtrage.",

    # confirm delete
    "Delete %(object_type)s": "Supprimer %(object_type)s",
    "Are you sure you want to delete <strong>%(object_name)s</strong>?": "Voulez-vous vraiment supprimer <strong>%(object_name)s</strong> ?",
    "book": "le livre",
    "category": "la catégorie",
    "author": "l'auteur",
    "sale": "la vente",
    "reorder": "le réapprovisionnement",

    # Forms
    "Author Form": "Formulaire auteur",
    "Category Form": "Formulaire catégorie",
    "Book Form": "Formulaire livre",
    "Sale Form": "Formulaire vente",
    "Profile Photo": "Photo de profil",
    "ISBN": "ISBN",
    "Category name": "Nom de la catégorie",
    "Author name": "Nom de l'auteur",
    "Channel (optional)": "Canal (facultatif)",
    "Password": "Mot de passe",
    "Confirm password": "Confirmer le mot de passe",
    "Username": "Nom d'utilisateur",
    "Email": "E-mail",
    "Verification code": "Code de vérification",
    "Verify": "Vérifier",
    "Verify your email": "Vérifiez votre e-mail",
    "Resend code": "Renvoyer le code",
    "Access code": "Code d'accès",
    "Activate": "Activer",
    "Activate your account": "Activez votre compte",
    "Create an account": "Créer un compte",
    "Sign up": "S'inscrire",
    "Already have an account?": "Vous avez déjà un compte ?",
    "Log in": "Se connecter",
    "Don't have an account?": "Vous n'avez pas de compte ?",
    "Create one": "Créez-en un",
    "Your username and password did not match.": "Votre nom d'utilisateur et votre mot de passe ne correspondent pas.",
    "We sent a 6-digit verification code to <strong>%(email)s</strong>. Enter it below to continue.":
        "Nous avons envoyé un code de vérification à 6 chiffres à <strong>%(email)s</strong>. Saisissez-le ci-dessous pour continuer.",
    "Your email is verified. Enter the access code you received from the RumiPress owner to activate your account and start using RumiPress.":
        "Votre e-mail est vérifié. Saisissez le code d'accès reçu du propriétaire de RumiPress pour activer votre compte et commencer à utiliser RumiPress.",

    # Dashboard
    "Overview of catalog, stock, and sales performance.": "Aperçu du catalogue, du stock et des performances des ventes.",
    "Full Report": "Rapport complet",
    "Total books": "Total des livres",
    "Total units sold": "Total des unités vendues",
    "Low stock items": "Articles en stock faible",
    "Total revenue": "Revenu total",
    "Total profit": "Profit total",
    "Low Stock": "Stock faible",
    "Top Sellers": "Meilleures ventes",
    "Recent Sales": "Ventes récentes",
    "Title": "Titre",
    "Threshold": "Seuil",
    "Low": "Faible",
    "Nothing is below its reorder threshold.": "Aucun article n'est sous son seuil de réapprovisionnement.",
    "View all %(low_stock_count)s low stock books": "Voir les %(low_stock_count)s livres en stock faible",
    "Category": "Catégorie",
    "Units sold": "Unités vendues",
    "No sales recorded yet.": "Aucune vente enregistrée pour le moment.",
    "Date": "Date",
    "Book": "Livre",
    "Quantity": "Quantité",
    "Revenue": "Revenu",
    "Channel": "Canal",
    "View all sales": "Voir toutes les ventes",
    "Revenue & Profit by Category": "Revenus et profits par catégorie",
    "Sales Trend": "Tendance des ventes",
    "Units Sold": "Unités vendues",
    "Profit": "Profit",
    "Total purchase cost": "Coût total des achats",
    "Purchase Cost": "Coût d'achat",
    "Stock Value": "Valeur du stock",
    "Total inventory value": "Valeur totale de l'inventaire",
    "Suggested Qty": "Quantité suggérée",
    "Reorder Suggestions": "Suggestions de réapprovisionnement",
    "Suggestions": "Suggestions",
    "Books that are low on stock or projected to run out soon, based on recent sales.": "Livres en faible stock ou dont la rupture est prévue prochainement, sur la base des ventes récentes.",
    "Avg. daily sales": "Ventes quotidiennes moy.",
    "Days of stock left": "Jours de stock restants",
    "Suggested quantity": "Quantité suggérée",
    "No reorder suggestions right now.": "Aucune suggestion de réapprovisionnement pour le moment.",
    "Adjust": "Ajuster",
    "Adjust Stock": "Ajuster le stock",
    "Adjustment History": "Historique des ajustements",
    "Stock Adjustments": "Ajustements de stock",
    "History of manual stock corrections.": "Historique des corrections manuelles de stock.",
    "Back to Stock": "Retour au stock",
    "Change": "Variation",
    "Resulting stock": "Stock résultant",
    "Use a positive number to add stock, negative to remove.": "Utilisez un nombre positif pour ajouter du stock, négatif pour en retirer.",
    "Change cannot be zero.": "La variation ne peut pas être nulle.",
    "This would reduce stock below zero (current stock: %(stock)s).": "Cela réduirait le stock en dessous de zéro (stock actuel : %(stock)s).",
    "Stock adjustment recorded.": "Ajustement de stock enregistré.",
    "No stock adjustments recorded yet.": "Aucun ajustement de stock enregistré pour le moment.",
    "Damaged": "Endommagé",
    "Lost": "Perdu",
    "Found": "Retrouvé",
    "Correction": "Correction",
    "Other": "Autre",

    # Book detail
    "Back to Books": "Retour aux livres",
    "Details": "Détails",
    "Publisher": "Éditeur",
    "Published": "Publié",
    "Distribution Expense": "Frais de distribution",
    "Inventory & Sales": "Inventaire et ventes",
    "Stock on hand": "Stock disponible",
    "Reorder threshold": "Seuil de réapprovisionnement",
    "Unit Price": "Prix unitaire",
    "No sales recorded for this book yet.": "Aucune vente enregistrée pour ce livre pour le moment.",

    # Book list
    "CSV": "CSV",
    "Excel": "Excel",
    "PDF": "PDF",
    "Search": "Rechercher",
    "Title, author, publisher, ISBN": "Titre, auteur, éditeur, ISBN",
    "All categories": "Toutes les catégories",
    "All publishers": "Tous les éditeurs",
    "Sort": "Trier",
    "Title A-Z": "Titre A-Z",
    "Title Z-A": "Titre Z-A",
    "Oldest first": "Plus anciens d'abord",
    "Newest first": "Plus récents d'abord",
    "Lowest expense": "Dépense la plus faible",
    "Highest expense": "Dépense la plus élevée",
    "Apply": "Appliquer",
    "Reset": "Réinitialiser",
    "Author": "Auteur",
    "Expense": "Dépense",
    "No books match the current filters.": "Aucun livre ne correspond aux filtres actuels.",
    "No books in your catalog yet.": "Aucun livre dans votre catalogue pour le moment.",
    "Add your first book": "Ajoutez votre premier livre",
    "%(count)s book(s) found": "%(count)s livre(s) trouvé(s)",
    'Search: "%(q)s"': 'Recherche : « %(q)s »',
    "Category: %(name)s": "Catégorie : %(name)s",
    "Author: %(name)s": "Auteur : %(name)s",
    "Publisher: %(name)s": "Éditeur : %(name)s",
    "Book pages": "Pages de livres",
    "Previous": "Précédent",
    "Next": "Suivant",
    "Page %(number)s of %(total)s": "Page %(number)s sur %(total)s",

    # Sales
    "Track book sales and revenue.": "Suivez les ventes de livres et les revenus.",
    "Sale pages": "Pages de ventes",

    # Stock
    "Review stock on hand and reorder thresholds.": "Consultez le stock disponible et les seuils de réapprovisionnement.",
    "Show all": "Tout afficher",
    "Low stock only": "Stock faible uniquement",
    "Status": "Statut",
    "Low stock": "Stock faible",
    "OK": "OK",
    "No books to show.": "Aucun livre à afficher.",

    # Reorders
    "Reorders": "Réapprovisionnements",
    "Track stock reorders from pending to received.": "Suivez les réapprovisionnements de stock, de la demande à la réception.",
    "All": "Tous",
    "Pending": "En attente",
    "Ordered": "Commandé",
    "Received": "Reçu",
    "Cancelled": "Annulé",
    "Note": "Note",
    "No reorders yet.": "Aucun réapprovisionnement pour le moment.",
    "Mark as ordered": "Marquer comme commandé",
    "Mark as received": "Marquer comme reçu",
    "Cancel reorder": "Annuler le réapprovisionnement",
    "Reorder pages": "Pages de réapprovisionnements",
    "Reorder Form": "Formulaire de réapprovisionnement",
    "'%(title)s' currently has %(stock)s in stock (threshold %(threshold)s).":
        "« %(title)s » a actuellement %(stock)s en stock (seuil %(threshold)s).",
    "Note (optional)": "Note (facultative)",
    "Reorder": "Réapprovisionner",
    "Reorder created.": "Réapprovisionnement créé.",
    "Reorder marked as ordered.": "Réapprovisionnement marqué comme commandé.",
    "Reorder received and stock updated.": "Réapprovisionnement reçu et stock mis à jour.",
    "Reorder cancelled.": "Réapprovisionnement annulé.",
    "This reorder can't be updated from its current status.": "Ce réapprovisionnement ne peut pas être mis à jour depuis son statut actuel.",
    "Reorder deleted.": "Réapprovisionnement supprimé.",
    "Only cancelled reorders can be deleted.": "Seuls les réapprovisionnements annulés peuvent être supprimés.",
    "Created": "Créé",
    "Supplier": "Fournisseur",
    "Unit Cost": "Coût unitaire",
    "Total Cost": "Coût total",

    # Suppliers
    "Suppliers": "Fournisseurs",
    "Add Supplier": "Ajouter un fournisseur",
    "Manage the suppliers used for stock reorders.": "Gérez les fournisseurs utilisés pour les réapprovisionnements de stock.",
    "No suppliers yet.": "Aucun fournisseur pour le moment.",
    "Contact name": "Nom du contact",
    "Contact name (optional)": "Nom du contact (facultatif)",
    "Phone": "Téléphone",
    "Notes": "Notes",
    "Notes (optional)": "Notes (facultatives)",
    "No supplier": "Aucun fournisseur",
    "Supplier created.": "Fournisseur créé.",
    "Supplier updated.": "Fournisseur mis à jour.",
    "Supplier deleted.": "Fournisseur supprimé.",
    "Remove this supplier from its reorders before deleting it.":
        "Retirez ce fournisseur de ses réapprovisionnements avant de le supprimer.",
    "This supplier is linked to reorders and cannot be deleted yet.":
        "Ce fournisseur est lié à des réapprovisionnements et ne peut pas encore être supprimé.",
    "supplier": "fournisseur",
    "Supplier Form": "Formulaire de fournisseur",

    # Returns
    "Returns": "Retours",
    "Sales returns and refunds.": "Retours de ventes et remboursements.",
    "Back to Sales": "Retour aux ventes",
    "Reason": "Motif",
    "Reason (optional)": "Motif (facultatif)",
    "Refund amount": "Montant du remboursement",
    "No returns recorded yet.": "Aucun retour enregistré pour le moment.",
    "Return pages": "Pages de retours",
    "Return": "Retour",
    "Return Form": "Formulaire de retour",
    "'%(title)s' sale on %(date)s has %(quantity)s unit(s) available to return.":
        "La vente de « %(title)s » du %(date)s a %(quantity)s unité(s) disponible(s) au retour.",
    "This sale has already been fully returned.": "Cette vente a déjà été entièrement retournée.",
    "Cannot return more than the %(quantity)s sold.":
        "Impossible de retourner plus que les %(quantity)s vendues.",
    "Return recorded and stock updated.": "Retour enregistré et stock mis à jour.",
    "Return deleted.": "Retour supprimé.",
    "return": "retour",

    # Report
    "Distribution Report": "Rapport de distribution",
    "Review distribution expenses by category.": "Consultez les frais de distribution par catégorie.",
    "Year": "Année",
    "From": "De",
    "To": "À",
    "All years": "Toutes les années",
    "Books in report": "Livres dans le rapport",
    "Total expense": "Dépense totale",
    "Distribution Expenses": "Frais de distribution",
    "Expenses by Category": "Dépenses par catégorie",

    # Model field labels (verbose_name)
    "Subtitle": "Sous-titre",
    "Published date": "Date de publication",
    "Distribution expense": "Frais de distribution",
    "Unit price": "Prix unitaire",
    "Unit cost": "Coût unitaire",
    "Sale date": "Date de vente",
    "Avatar": "Avatar",

    # Export headers / titles
    "Published Date": "Date de publication",
    "Rumi Press Books": "Rumi Press - Livres",
    "Rumi Press Sales": "Rumi Press - Ventes",
    "Rumi Press Reorders": "Rumi Press - Réapprovisionnements",

    # Views / forms messages
    "Good morning": "Bonjour",
    "Good afternoon": "Bon après-midi",
    "Good evening": "Bonsoir",
    "Book created.": "Livre créé.",
    "Book updated.": "Livre mis à jour.",
    "Book deleted.": "Livre supprimé.",
    "Category created.": "Catégorie créée.",
    "Category updated.": "Catégorie mise à jour.",
    "Category deleted.": "Catégorie supprimée.",
    "Author created.": "Auteur créé.",
    "Author updated.": "Auteur mis à jour.",
    "Author deleted.": "Auteur supprimé.",
    "Sale recorded.": "Vente enregistrée.",
    "Sale updated.": "Vente mise à jour.",
    "Sale deleted.": "Vente supprimée.",
    "Profile photo updated.": "Photo de profil mise à jour.",
    "Move or delete this category's books before deleting the category.":
        "Déplacez ou supprimez les livres de cette catégorie avant de la supprimer.",
    "This category contains books and cannot be deleted yet.":
        "Cette catégorie contient des livres et ne peut pas encore être supprimée.",
    "Remove this author from their books before deleting them.":
        "Retirez cet auteur de ses livres avant de le supprimer.",
    "This author is linked to books and cannot be deleted yet.":
        "Cet auteur est lié à des livres et ne peut pas encore être supprimé.",
    "Cannot record sale: '%(title)s' only has %(stock)s in stock.":
        "Impossible d'enregistrer la vente : « %(title)s » n'a que %(stock)s en stock.",
    "Cannot update sale: '%(title)s' only has %(stock)s available.":
        "Impossible de mettre à jour la vente : « %(title)s » n'a que %(stock)s disponible.",
    "Low stock: '%(title)s' has %(stock)s remaining (reorder threshold %(threshold)s).":
        "Stock faible : « %(title)s » a %(stock)s restant (seuil de réapprovisionnement %(threshold)s).",
    "'%(title)s' now has %(stock)s in stock.":
        "« %(title)s » a maintenant %(stock)s en stock.",
    "Sorry, something went wrong talking to the AI assistant. Please try again.":
        "Désolé, une erreur est survenue avec l'assistant IA. Veuillez réessayer.",
    "A new verification code has been sent to your email.":
        "Un nouveau code de vérification a été envoyé à votre e-mail.",
    "That code is invalid or has expired.": "Ce code est invalide ou a expiré.",
    "That access code is invalid, used, or expired.": "Ce code d'accès est invalide, déjà utilisé ou expiré.",
    "Your account is active. Welcome to RumiPress!": "Votre compte est actif. Bienvenue sur RumiPress !",
    "That username is already taken.": "Ce nom d'utilisateur est déjà pris.",
    "An account with that email already exists.": "Un compte avec cet e-mail existe déjà.",
    "The two password fields didn't match.": "Les deux champs de mot de passe ne correspondent pas.",

    # Learning quotes
    '"The beautiful thing about learning is that no one can take it away from you." — B.B. King':
        "« La belle chose à propos de l'apprentissage, c'est que personne ne peut vous l'enlever. » — B.B. King",
    '"Live as if you were to die tomorrow. Learn as if you were to live forever." — Mahatma Gandhi':
        "« Vis comme si tu devais mourir demain. Apprends comme si tu devais vivre toujours. » — Mahatma Gandhi",
    '"An investment in knowledge always pays the best interest." — Benjamin Franklin':
        "« Un investissement dans la connaissance rapporte toujours les meilleurs intérêts. » — Benjamin Franklin",
    "\"The capacity to learn is a gift; the ability to learn is a skill; the willingness to learn is a choice.\" — Brian Herbert":
        "« La capacité d'apprendre est un don ; l'aptitude à apprendre est une compétence ; la volonté d'apprendre est un choix. » — Brian Herbert",
    "\"Develop a passion for learning. If you do, you will never cease to grow.\" — Anthony J. D'Angelo":
        "« Développez une passion pour l'apprentissage. Si vous le faites, vous ne cesserez jamais de grandir. » — Anthony J. D'Angelo",
    '"Each small task of everyday life is part of the total harmony of the universe." — Saint Therese':
        "« Chaque petite tâche de la vie quotidienne fait partie de l'harmonie totale de l'univers. » — Sainte Thérèse",
    "\"Growth is painful. Change is painful. But nothing is as painful as staying stuck somewhere you don't belong.\" — N.R. Narayana Murthy":
        "« Grandir est douloureux. Changer est douloureux. Mais rien n'est aussi douloureux que de rester coincé là où l'on n'a pas sa place. » — N.R. Narayana Murthy",
    '"The expert in anything was once a beginner." — Helen Hayes':
        "« L'expert en toute chose a d'abord été un débutant. » — Helen Hayes",
    '"Success is the sum of small efforts repeated day in and day out." — Robert Collier':
        "« Le succès est la somme de petits efforts répétés jour après jour. » — Robert Collier",
    "\"You don't have to be great to start, but you have to start to be great.\" — Zig Ziglar":
        "« Vous n'avez pas besoin d'être excellent pour commencer, mais vous devez commencer pour être excellent. » — Zig Ziglar",
    '"A room without books is like a body without a soul." — Marcus Tullius Cicero':
        "« Une pièce sans livres est comme un corps sans âme. » — Marcus Tullius Cicero",
    '"Books are a uniquely portable magic." — Stephen King':
        "« Les livres sont une magie particulièrement portable. » — Stephen King",
    '"Today a reader, tomorrow a leader." — Margaret Fuller':
        "« Lecteur aujourd'hui, leader demain. » — Margaret Fuller",
    '"Reading is to the mind what exercise is to the body." — Joseph Addison':
        "« La lecture est à l'esprit ce que l'exercice est au corps. » — Joseph Addison",
    '"Once you learn to read, you will be forever free." — Frederick Douglass':
        "« Une fois que vous apprenez à lire, vous serez libre pour toujours. » — Frederick Douglass",
    '"I have always imagined that Paradise will be a kind of library." — Jorge Luis Borges':
        "« J'ai toujours imaginé que le Paradis serait une sorte de bibliothèque. » — Jorge Luis Borges",
    '"So many books, so little time." — Frank Zappa':
        "« Tant de livres, si peu de temps. » — Frank Zappa",
    '"There is no friend as loyal as a book." — Ernest Hemingway':
        "« Il n'y a pas d'ami aussi loyal qu'un livre. » — Ernest Hemingway",
    '"A reader lives a thousand lives before he dies. The man who never reads lives only one." — George R.R. Martin':
        "« Un lecteur vit mille vies avant de mourir. L'homme qui ne lit jamais n'en vit qu'une. » — George R.R. Martin",
    '"Books are mirrors: you only see in them what you already have inside you." — Carlos Ruiz Zafón':
        "« Les livres sont des miroirs : on n'y voit que ce que l'on porte déjà en soi. » — Carlos Ruiz Zafón",
    '"Every book has a destination, and every reader has a journey." — Book Distribution Philosophy':
        "« Chaque livre a une destination, et chaque lecteur a un voyage. » — Philosophie de la distribution de livres",
    '"We do not simply move books; we move knowledge, ideas, and imagination." — Book Distribution Philosophy':
        "« Nous ne déplaçons pas simplement des livres ; nous déplaçons des connaissances, des idées et de l'imagination. » — Philosophie de la distribution de livres",
    '"A warehouse full of books is a warehouse full of possibilities." — Book Distribution Philosophy':
        "« Un entrepôt plein de livres est un entrepôt plein de possibilités. » — Philosophie de la distribution de livres",
    '"Every delivered book is a new story beginning somewhere." — Book Distribution Philosophy':
        "« Chaque livre livré est une nouvelle histoire qui commence quelque part. » — Philosophie de la distribution de livres",
    '"Behind every order is a reader waiting for discovery." — Book Distribution Philosophy':
        "« Derrière chaque commande se trouve un lecteur en attente de découverte. » — Philosophie de la distribution de livres",
    '"Distribution turns printed pages into shared experiences." — Book Distribution Philosophy':
        "« La distribution transforme les pages imprimées en expériences partagées. » — Philosophie de la distribution de livres",
    '"Every package carries imagination, knowledge, and opportunity." — Book Distribution Philosophy':
        "« Chaque colis transporte de l'imagination, des connaissances et des opportunités. » — Philosophie de la distribution de livres",
    '"A distributor is the bridge between authors and readers." — Book Distribution Philosophy':
        "« Un distributeur est le pont entre les auteurs et les lecteurs. » — Philosophie de la distribution de livres",
    '"The journey of knowledge begins with accessibility." — Book Distribution Philosophy':
        "« Le voyage de la connaissance commence par l'accessibilité. » — Philosophie de la distribution de livres",
    '"Books travel so minds can explore." — Book Distribution Philosophy':
        "« Les livres voyagent pour que les esprits puissent explorer. » — Philosophie de la distribution de livres",
    '"The goal as a company is to have customer service that is not just the best but legendary." — Sam Walton':
        "« L'objectif d'une entreprise est d'avoir un service client qui ne soit pas seulement le meilleur, mais légendaire. » — Sam Walton",
    '"Quality is the best business plan." — John Lasseter':
        "« La qualité est le meilleur plan d'affaires. » — John Lasseter",
    '"Great things in business are never done by one person. They are done by a team of people." — Steve Jobs':
        "« Les grandes choses en affaires ne sont jamais réalisées par une seule personne. Elles sont réalisées par une équipe. » — Steve Jobs",
    '"Efficiency is doing better what is already being done." — Peter Drucker':
        "« L'efficacité consiste à mieux faire ce qui est déjà fait. » — Peter Drucker",
    '"The best way to predict the future is to create it." — Peter Drucker':
        "« La meilleure façon de prédire l'avenir est de le créer. » — Peter Drucker",
}

# ---------------------------------------------------------------------------
# Arabic translations
# ---------------------------------------------------------------------------
AR = {
    # Navigation / layout
    "Dashboard": "لوحة التحكم",
    "Catalog": "الكتالوج",
    "Books": "الكتب",
    "Stock": "المخزون",
    "Categories": "التصنيفات",
    "Authors": "المؤلفون",
    "Sales": "المبيعات",
    "Reports": "التقارير",
    "Help": "المساعدة",
    "About": "حول",
    "Logout": "تسجيل الخروج",
    "Theme": "المظهر",
    "Login": "تسجيل الدخول",
    "Language": "اللغة",
    "RumiPress Assistant": "مساعد روومي برِس",
    "Ask a question...": "اطرح سؤالاً...",
    "Toggle navigation": "إظهار/إخفاء التنقل",
    "Open AI assistant": "فتح المساعد الذكي",
    "Thinking...": "جارٍ التفكير...",
    "Sorry, something went wrong.": "عذرًا، حدث خطأ ما.",

    # About page
    "About Rumi Press": "حول روومي برِس",
    "What each part of the system does.": "ما تقوم به كل وحدة من النظام.",
    "Profile": "الملف الشخصي",
    "A quick overview of the whole catalog: total books, total units sold, low stock count, total revenue and profit, a revenue/profit-by-category chart, a sales trend chart, the books currently low on stock, the top sellers, and the most recent sales.":
        "نظرة سريعة على الكتالوج بالكامل: إجمالي الكتب، إجمالي الوحدات المباعة، عدد الأصناف منخفضة المخزون، إجمالي الإيرادات والأرباح، رسم بياني للإيرادات/الأرباح حسب التصنيف، رسم بياني لاتجاه المبيعات، الكتب منخفضة المخزون حاليًا، الأكثر مبيعًا، وأحدث المبيعات.",
    "The catalog of titles: ISBN, title, subtitle, authors, publisher, publish date, category and distribution expense. Search, filter by category/publisher, sort, and export the list to CSV, Excel or PDF.":
        "كتالوج العناوين: الرقم الدولي، العنوان، العنوان الفرعي، المؤلفون، الناشر، تاريخ النشر، التصنيف ومصاريف التوزيع. يمكنك البحث والتصفية حسب التصنيف/الناشر والترتيب وتصدير القائمة إلى CSV أو Excel أو PDF.",
    "Stock on hand and reorder thresholds for every book. Books at or below their threshold are flagged \"Low stock\" and can be filtered to a low-stock-only view.":
        "المخزون المتاح وحدود إعادة الطلب لكل كتاب. توضع علامة \"مخزون منخفض\" على الكتب التي تساوي حدها أو تقل عنه، ويمكن تصفيتها في عرض خاص بالمخزون المنخفض فقط.",
    "Groupings used to organize books for filtering and reporting. Each category shows how many books belong to it, and can be added, edited or deleted (categories still in use can't be deleted).":
        "تجميعات تُستخدم لتنظيم الكتب لأغراض التصفية والتقارير. تعرض كل فئة عدد الكتب التابعة لها، ويمكن إضافتها أو تعديلها أو حذفها (لا يمكن حذف الفئات المستخدمة حاليًا).",
    "The list of authors linked to books, with a count of how many books each author has. Authors can be added, edited or removed once they are no longer linked to any book.":
        "قائمة المؤلفين المرتبطين بالكتب، مع عدد الكتب لكل مؤلف. يمكن إضافة المؤلفين أو تعديلهم أو إزالتهم بعد أن لا يعودوا مرتبطين بأي كتاب.",
    "Every sale transaction: book, quantity, unit price, revenue, date and channel. Recording a sale reduces the book's stock on hand and shows a remaining-stock notification; a sale is refused if it would exceed available stock. The list can be exported to CSV, Excel or PDF.":
        "كل معاملة بيع: الكتاب، الكمية، سعر الوحدة، الإيراد، التاريخ والقناة. يؤدي تسجيل عملية بيع إلى تقليل المخزون المتاح للكتاب وإظهار إشعار بالمخزون المتبقي؛ ويُرفض البيع إذا كان يتجاوز المخزون المتاح. يمكن تصدير القائمة إلى CSV أو Excel أو PDF.",
    "A filterable distribution report: total books, expense, revenue and profit, with charts for expenses and revenue/profit by category and a sales trend over time. Results can be exported to CSV, Excel or PDF.":
        "تقرير توزيع قابل للتصفية: إجمالي الكتب والمصاريف والإيرادات والأرباح، مع رسوم بيانية للمصاريف والإيرادات/الأرباح حسب التصنيف واتجاه المبيعات بمرور الوقت. يمكن تصدير النتائج إلى CSV أو Excel أو PDF.",
    "Each user can upload a profile photo, shown in the sidebar. Access to each section above is controlled by permissions, so what you see depends on your assigned role.":
        "يمكن لكل مستخدم رفع صورة شخصية تُعرض في الشريط الجانبي. يتم التحكم في الوصول إلى كل قسم أعلاه عبر الصلاحيات، فما تراه يعتمد على دورك المعيّن.",

    # Common
    "Edit": "تعديل",
    "Delete": "حذف",
    "Save": "حفظ",
    "Cancel": "إلغاء",
    "Name": "الاسم",
    "Actions": "إجراءات",
    "Add Author": "إضافة مؤلف",
    "Add Category": "إضافة تصنيف",
    "Add Book": "إضافة كتاب",
    "Add Sale": "إضافة عملية بيع",
    "No authors yet.": "لا يوجد مؤلفون حتى الآن.",
    "No categories yet.": "لا توجد تصنيفات حتى الآن.",
    "Manage the authors linked to your books.": "إدارة المؤلفين المرتبطين بكتبك.",
    "Manage the catalog, filter records, and open reports.": "إدارة الكتالوج وتصفية السجلات وفتح التقارير.",
    "Group books for reporting and filtering.": "تجميع الكتب لأغراض التقارير والتصفية.",

    # confirm delete
    "Delete %(object_type)s": "حذف %(object_type)s",
    "Are you sure you want to delete <strong>%(object_name)s</strong>?": "هل أنت متأكد من حذف <strong>%(object_name)s</strong>؟",
    "book": "الكتاب",
    "category": "التصنيف",
    "author": "المؤلف",
    "sale": "عملية البيع",
    "reorder": "طلب إعادة التزويد",

    # Forms
    "Author Form": "نموذج المؤلف",
    "Category Form": "نموذج التصنيف",
    "Book Form": "نموذج الكتاب",
    "Sale Form": "نموذج البيع",
    "Profile Photo": "الصورة الشخصية",
    "ISBN": "الرقم الدولي ISBN",
    "Category name": "اسم التصنيف",
    "Author name": "اسم المؤلف",
    "Channel (optional)": "القناة (اختياري)",
    "Password": "كلمة المرور",
    "Confirm password": "تأكيد كلمة المرور",
    "Username": "اسم المستخدم",
    "Email": "البريد الإلكتروني",
    "Verification code": "رمز التحقق",
    "Verify": "تحقق",
    "Verify your email": "تحقق من بريدك الإلكتروني",
    "Resend code": "إعادة إرسال الرمز",
    "Access code": "رمز الدخول",
    "Activate": "تفعيل",
    "Activate your account": "فعّل حسابك",
    "Create an account": "إنشاء حساب",
    "Sign up": "تسجيل",
    "Already have an account?": "هل لديك حساب بالفعل؟",
    "Log in": "تسجيل الدخول",
    "Don't have an account?": "ليس لديك حساب؟",
    "Create one": "أنشئ حسابًا",
    "Your username and password did not match.": "اسم المستخدم وكلمة المرور غير متطابقين.",
    "We sent a 6-digit verification code to <strong>%(email)s</strong>. Enter it below to continue.":
        "أرسلنا رمز تحقق مكونًا من 6 أرقام إلى <strong>%(email)s</strong>. أدخله أدناه للمتابعة.",
    "Your email is verified. Enter the access code you received from the RumiPress owner to activate your account and start using RumiPress.":
        "تم التحقق من بريدك الإلكتروني. أدخل رمز الدخول الذي استلمته من مالك روومي برِس لتفعيل حسابك وبدء استخدام روومي برِس.",

    # Dashboard
    "Overview of catalog, stock, and sales performance.": "نظرة عامة على الكتالوج والمخزون وأداء المبيعات.",
    "Full Report": "التقرير الكامل",
    "Total books": "إجمالي الكتب",
    "Total units sold": "إجمالي الوحدات المباعة",
    "Low stock items": "أصناف منخفضة المخزون",
    "Total revenue": "إجمالي الإيرادات",
    "Total profit": "إجمالي الأرباح",
    "Low Stock": "مخزون منخفض",
    "Top Sellers": "الأكثر مبيعًا",
    "Recent Sales": "أحدث المبيعات",
    "Title": "العنوان",
    "Threshold": "الحد",
    "Low": "منخفض",
    "Nothing is below its reorder threshold.": "لا يوجد عنصر تحت حد إعادة الطلب.",
    "View all %(low_stock_count)s low stock books": "عرض جميع الكتب منخفضة المخزون (%(low_stock_count)s)",
    "Category": "التصنيف",
    "Units sold": "الوحدات المباعة",
    "No sales recorded yet.": "لا توجد مبيعات مسجلة حتى الآن.",
    "Date": "التاريخ",
    "Book": "الكتاب",
    "Quantity": "الكمية",
    "Revenue": "الإيراد",
    "Channel": "القناة",
    "View all sales": "عرض جميع المبيعات",
    "Revenue & Profit by Category": "الإيراد والأرباح حسب التصنيف",
    "Sales Trend": "اتجاه المبيعات",
    "Units Sold": "الوحدات المباعة",
    "Profit": "الأرباح",
    "Total purchase cost": "إجمالي تكلفة الشراء",
    "Purchase Cost": "تكلفة الشراء",
    "Stock Value": "قيمة المخزون",
    "Total inventory value": "القيمة الإجمالية للمخزون",
    "Suggested Qty": "الكمية المقترحة",
    "Reorder Suggestions": "مقترحات إعادة الطلب",
    "Suggestions": "مقترحات",
    "Books that are low on stock or projected to run out soon, based on recent sales.": "الكتب منخفضة المخزون أو المتوقع نفادها قريبًا، بناءً على المبيعات الأخيرة.",
    "Avg. daily sales": "متوسط المبيعات اليومية",
    "Days of stock left": "الأيام المتبقية من المخزون",
    "Suggested quantity": "الكمية المقترحة",
    "No reorder suggestions right now.": "لا توجد مقترحات لإعادة الطلب حاليًا.",
    "Adjust": "تعديل",
    "Adjust Stock": "تعديل المخزون",
    "Adjustment History": "سجل التعديلات",
    "Stock Adjustments": "تعديلات المخزون",
    "History of manual stock corrections.": "سجل التصحيحات اليدوية للمخزون.",
    "Back to Stock": "العودة إلى المخزون",
    "Change": "التغيير",
    "Resulting stock": "المخزون الناتج",
    "Use a positive number to add stock, negative to remove.": "استخدم رقمًا موجبًا لإضافة المخزون وسالبًا لإزالته.",
    "Change cannot be zero.": "لا يمكن أن يكون التغيير صفرًا.",
    "This would reduce stock below zero (current stock: %(stock)s).": "سيؤدي هذا إلى خفض المخزون إلى أقل من الصفر (المخزون الحالي: %(stock)s).",
    "Stock adjustment recorded.": "تم تسجيل تعديل المخزون.",
    "No stock adjustments recorded yet.": "لا توجد تعديلات مخزون مسجلة حتى الآن.",
    "Damaged": "تالف",
    "Lost": "مفقود",
    "Found": "تم العثور عليه",
    "Correction": "تصحيح",
    "Other": "أخرى",

    # Book detail
    "Back to Books": "العودة إلى الكتب",
    "Details": "التفاصيل",
    "Publisher": "الناشر",
    "Published": "تاريخ النشر",
    "Distribution Expense": "مصاريف التوزيع",
    "Inventory & Sales": "المخزون والمبيعات",
    "Stock on hand": "المخزون المتاح",
    "Reorder threshold": "حد إعادة الطلب",
    "Unit Price": "سعر الوحدة",
    "No sales recorded for this book yet.": "لا توجد مبيعات مسجلة لهذا الكتاب حتى الآن.",

    # Book list
    "CSV": "CSV",
    "Excel": "Excel",
    "PDF": "PDF",
    "Search": "بحث",
    "Title, author, publisher, ISBN": "العنوان، المؤلف، الناشر، الرقم الدولي",
    "All categories": "جميع التصنيفات",
    "All publishers": "جميع الناشرين",
    "Sort": "ترتيب",
    "Title A-Z": "العنوان من أ إلى ي",
    "Title Z-A": "العنوان من ي إلى أ",
    "Oldest first": "الأقدم أولاً",
    "Newest first": "الأحدث أولاً",
    "Lowest expense": "أقل المصاريف",
    "Highest expense": "أعلى المصاريف",
    "Apply": "تطبيق",
    "Reset": "إعادة تعيين",
    "Author": "المؤلف",
    "Expense": "المصاريف",
    "No books match the current filters.": "لا توجد كتب مطابقة للفلاتر الحالية.",
    "No books in your catalog yet.": "لا توجد كتب في كتالوجك حتى الآن.",
    "Add your first book": "أضف أول كتاب لك",
    "%(count)s book(s) found": "تم العثور على %(count)s كتاب",
    'Search: "%(q)s"': 'بحث: "%(q)s"',
    "Category: %(name)s": "الفئة: %(name)s",
    "Author: %(name)s": "المؤلف: %(name)s",
    "Publisher: %(name)s": "الناشر: %(name)s",
    "Book pages": "صفحات الكتب",
    "Previous": "السابق",
    "Next": "التالي",
    "Page %(number)s of %(total)s": "صفحة %(number)s من %(total)s",

    # Sales
    "Track book sales and revenue.": "تتبع مبيعات الكتب والإيرادات.",
    "Sale pages": "صفحات المبيعات",

    # Stock
    "Review stock on hand and reorder thresholds.": "مراجعة المخزون المتاح وحدود إعادة الطلب.",
    "Show all": "عرض الكل",
    "Low stock only": "المخزون المنخفض فقط",
    "Status": "الحالة",
    "Low stock": "مخزون منخفض",
    "OK": "جيد",
    "No books to show.": "لا توجد كتب لعرضها.",

    # Reorders
    "Reorders": "إعادة الطلب",
    "Track stock reorders from pending to received.": "تتبع طلبات إعادة التزويد من الانتظار إلى الاستلام.",
    "All": "الكل",
    "Pending": "قيد الانتظار",
    "Ordered": "تم الطلب",
    "Received": "تم الاستلام",
    "Cancelled": "ملغى",
    "Note": "ملاحظة",
    "No reorders yet.": "لا توجد طلبات إعادة تزويد حتى الآن.",
    "Mark as ordered": "وضع علامة كمطلوب",
    "Mark as received": "وضع علامة كمستلم",
    "Cancel reorder": "إلغاء طلب إعادة التزويد",
    "Reorder pages": "صفحات إعادة الطلب",
    "Reorder Form": "نموذج إعادة الطلب",
    "'%(title)s' currently has %(stock)s in stock (threshold %(threshold)s).":
        "'%(title)s' لديه حاليًا %(stock)s في المخزون (الحد %(threshold)s).",
    "Note (optional)": "ملاحظة (اختياري)",
    "Reorder": "إعادة الطلب",
    "Reorder created.": "تم إنشاء طلب إعادة التزويد.",
    "Reorder marked as ordered.": "تم وضع علامة على الطلب كمطلوب.",
    "Reorder received and stock updated.": "تم استلام الطلب وتحديث المخزون.",
    "Reorder cancelled.": "تم إلغاء طلب إعادة التزويد.",
    "This reorder can't be updated from its current status.": "لا يمكن تحديث هذا الطلب من حالته الحالية.",
    "Reorder deleted.": "تم حذف طلب إعادة التزويد.",
    "Only cancelled reorders can be deleted.": "يمكن حذف طلبات إعادة التزويد الملغاة فقط.",
    "Created": "تاريخ الإنشاء",
    "Supplier": "المورّد",
    "Unit Cost": "تكلفة الوحدة",
    "Total Cost": "التكلفة الإجمالية",

    # Suppliers
    "Suppliers": "الموردون",
    "Add Supplier": "إضافة مورّد",
    "Manage the suppliers used for stock reorders.": "إدارة الموردين المستخدمين لإعادة تزويد المخزون.",
    "No suppliers yet.": "لا يوجد موردون حتى الآن.",
    "Contact name": "اسم جهة الاتصال",
    "Contact name (optional)": "اسم جهة الاتصال (اختياري)",
    "Phone": "الهاتف",
    "Notes": "ملاحظات",
    "Notes (optional)": "ملاحظات (اختياري)",
    "No supplier": "بدون مورّد",
    "Supplier created.": "تم إنشاء المورّد.",
    "Supplier updated.": "تم تحديث المورّد.",
    "Supplier deleted.": "تم حذف المورّد.",
    "Remove this supplier from its reorders before deleting it.":
        "أزل هذا المورّد من طلبات إعادة التزويد قبل حذفه.",
    "This supplier is linked to reorders and cannot be deleted yet.":
        "هذا المورّد مرتبط بطلبات إعادة تزويد ولا يمكن حذفه حاليًا.",
    "supplier": "مورّد",
    "Supplier Form": "نموذج المورّد",

    # Returns
    "Returns": "المرتجعات",
    "Sales returns and refunds.": "مرتجعات المبيعات والمبالغ المستردة.",
    "Back to Sales": "العودة إلى المبيعات",
    "Reason": "السبب",
    "Reason (optional)": "السبب (اختياري)",
    "Refund amount": "مبلغ الاسترداد",
    "No returns recorded yet.": "لا توجد مرتجعات مسجلة حتى الآن.",
    "Return pages": "صفحات المرتجعات",
    "Return": "إرجاع",
    "Return Form": "نموذج الإرجاع",
    "'%(title)s' sale on %(date)s has %(quantity)s unit(s) available to return.":
        "بيع '%(title)s' في %(date)s لديه %(quantity)s وحدة متاحة للإرجاع.",
    "This sale has already been fully returned.": "تم إرجاع هذه المبيعة بالكامل من قبل.",
    "Cannot return more than the %(quantity)s sold.":
        "لا يمكن إرجاع أكثر من %(quantity)s المباعة.",
    "Return recorded and stock updated.": "تم تسجيل الإرجاع وتحديث المخزون.",
    "Return deleted.": "تم حذف الإرجاع.",
    "return": "إرجاع",

    # Report
    "Distribution Report": "تقرير التوزيع",
    "Review distribution expenses by category.": "مراجعة مصاريف التوزيع حسب التصنيف.",
    "Year": "السنة",
    "From": "من",
    "To": "إلى",
    "All years": "جميع السنوات",
    "Books in report": "الكتب في التقرير",
    "Total expense": "إجمالي المصاريف",
    "Distribution Expenses": "مصاريف التوزيع",
    "Expenses by Category": "المصاريف حسب التصنيف",

    # Model field labels (verbose_name)
    "Subtitle": "العنوان الفرعي",
    "Published date": "تاريخ النشر",
    "Distribution expense": "مصاريف التوزيع",
    "Unit price": "سعر الوحدة",
    "Unit cost": "تكلفة الوحدة",
    "Sale date": "تاريخ البيع",
    "Avatar": "الصورة الرمزية",

    # Export headers / titles
    "Published Date": "تاريخ النشر",
    "Rumi Press Books": "روومي برِس - الكتب",
    "Rumi Press Sales": "روومي برِس - المبيعات",
    "Rumi Press Reorders": "روومي برِس - إعادة الطلب",

    # Views / forms messages
    "Good morning": "صباح الخير",
    "Good afternoon": "مساء الخير",
    "Good evening": "مساء الخير",
    "Book created.": "تم إنشاء الكتاب.",
    "Book updated.": "تم تحديث الكتاب.",
    "Book deleted.": "تم حذف الكتاب.",
    "Category created.": "تم إنشاء التصنيف.",
    "Category updated.": "تم تحديث التصنيف.",
    "Category deleted.": "تم حذف التصنيف.",
    "Author created.": "تم إنشاء المؤلف.",
    "Author updated.": "تم تحديث المؤلف.",
    "Author deleted.": "تم حذف المؤلف.",
    "Sale recorded.": "تم تسجيل عملية البيع.",
    "Sale updated.": "تم تحديث عملية البيع.",
    "Sale deleted.": "تم حذف عملية البيع.",
    "Profile photo updated.": "تم تحديث الصورة الشخصية.",
    "Move or delete this category's books before deleting the category.":
        "انقل كتب هذا التصنيف أو احذفها قبل حذف التصنيف.",
    "This category contains books and cannot be deleted yet.":
        "يحتوي هذا التصنيف على كتب ولا يمكن حذفه حتى الآن.",
    "Remove this author from their books before deleting them.":
        "أزل هذا المؤلف من كتبه قبل حذفه.",
    "This author is linked to books and cannot be deleted yet.":
        "هذا المؤلف مرتبط بكتب ولا يمكن حذفه حتى الآن.",
    "Cannot record sale: '%(title)s' only has %(stock)s in stock.":
        "لا يمكن تسجيل البيع: '%(title)s' لديه %(stock)s فقط في المخزون.",
    "Cannot update sale: '%(title)s' only has %(stock)s available.":
        "لا يمكن تحديث البيع: '%(title)s' لديه %(stock)s فقط متاح.",
    "Low stock: '%(title)s' has %(stock)s remaining (reorder threshold %(threshold)s).":
        "مخزون منخفض: '%(title)s' لديه %(stock)s متبقٍ (حد إعادة الطلب %(threshold)s).",
    "'%(title)s' now has %(stock)s in stock.":
        "'%(title)s' لديه الآن %(stock)s في المخزون.",
    "Sorry, something went wrong talking to the AI assistant. Please try again.":
        "عذرًا، حدث خطأ أثناء التواصل مع المساعد الذكي. حاول مرة أخرى.",
    "A new verification code has been sent to your email.":
        "تم إرسال رمز تحقق جديد إلى بريدك الإلكتروني.",
    "That code is invalid or has expired.": "هذا الرمز غير صالح أو منتهي الصلاحية.",
    "That access code is invalid, used, or expired.": "رمز الدخول هذا غير صالح أو مستخدم أو منتهي الصلاحية.",
    "Your account is active. Welcome to RumiPress!": "حسابك نشط الآن. مرحبًا بك في روومي برِس!",
    "That username is already taken.": "اسم المستخدم هذا مستخدم بالفعل.",
    "An account with that email already exists.": "يوجد حساب بهذا البريد الإلكتروني مسبقًا.",
    "The two password fields didn't match.": "حقلا كلمة المرور غير متطابقين.",

    # Learning quotes
    '"The beautiful thing about learning is that no one can take it away from you." — B.B. King':
        "«الشيء الجميل في التعلم هو أنه لا يمكن لأحد أن ينتزعه منك.» — بي بي كينغ",
    '"Live as if you were to die tomorrow. Learn as if you were to live forever." — Mahatma Gandhi':
        "«عش كما لو كنت ستموت غدًا. وتعلّم كما لو كنت ستعيش إلى الأبد.» — المهاتما غاندي",
    '"An investment in knowledge always pays the best interest." — Benjamin Franklin':
        "«الاستثمار في المعرفة يدفع دائمًا أفضل الفوائد.» — بنجامين فرانكلين",
    "\"The capacity to learn is a gift; the ability to learn is a skill; the willingness to learn is a choice.\" — Brian Herbert":
        "«القدرة على التعلم هبة؛ والمهارة في التعلم موهبة؛ والرغبة في التعلم اختيار.» — براين هربرت",
    "\"Develop a passion for learning. If you do, you will never cease to grow.\" — Anthony J. D'Angelo":
        "«طوّر شغفًا بالتعلم. فإن فعلت ذلك، لن تتوقف عن النمو أبدًا.» — أنتوني جي. دانجيلو",
    '"Each small task of everyday life is part of the total harmony of the universe." — Saint Therese':
        "«كل مهمة صغيرة في الحياة اليومية هي جزء من الانسجام الكامل للكون.» — القديسة تيريز",
    "\"Growth is painful. Change is painful. But nothing is as painful as staying stuck somewhere you don't belong.\" — N.R. Narayana Murthy":
        "«النمو مؤلم. التغيير مؤلم. ولكن لا شيء مؤلم بقدر أن تبقى عالقًا في مكان لا تنتمي إليه.» — إن آر نارايانا مورثي",
    '"The expert in anything was once a beginner." — Helen Hayes':
        "«الخبير في أي شيء كان مبتدئًا في يوم ما.» — هيلين هايز",
    '"Success is the sum of small efforts repeated day in and day out." — Robert Collier':
        "«النجاح هو مجموع جهود صغيرة تتكرر يومًا بعد يوم.» — روبرت كولير",
    "\"You don't have to be great to start, but you have to start to be great.\" — Zig Ziglar":
        "«لست مضطرًا لأن تكون عظيمًا لتبدأ، لكنك مضطر أن تبدأ لتكون عظيمًا.» — زيغ زيغلر",
    '"A room without books is like a body without a soul." — Marcus Tullius Cicero':
        "«غرفة بلا كتب مثل جسد بلا روح.» — ماركوس توليوس شيشرون",
    '"Books are a uniquely portable magic." — Stephen King':
        "«الكتب سحر فريد يمكن حمله في كل مكان.» — ستيفن كينغ",
    '"Today a reader, tomorrow a leader." — Margaret Fuller':
        "«قارئ اليوم، قائد الغد.» — مارغريت فولر",
    '"Reading is to the mind what exercise is to the body." — Joseph Addison':
        "«القراءة للعقل كالتمرين للجسد.» — جوزيف أديسون",
    '"Once you learn to read, you will be forever free." — Frederick Douglass':
        "«حين تتعلم القراءة، تصبح حرًا للأبد.» — فريدريك دوغلاس",
    '"I have always imagined that Paradise will be a kind of library." — Jorge Luis Borges':
        "«تخيلت دائمًا أن الجنة ستكون نوعًا من المكتبة.» — خورخي لويس بورخيس",
    '"So many books, so little time." — Frank Zappa':
        "«كتب كثيرة جدًا، ووقت قليل جدًا.» — فرانك زابا",
    '"There is no friend as loyal as a book." — Ernest Hemingway':
        "«لا يوجد صديق وفيّ كالكتاب.» — إرنست همنغواي",
    '"A reader lives a thousand lives before he dies. The man who never reads lives only one." — George R.R. Martin':
        "«القارئ يعيش ألف حياة قبل أن يموت. والشخص الذي لا يقرأ يعيش حياة واحدة فقط.» — جورج آر آر مارتن",
    '"Books are mirrors: you only see in them what you already have inside you." — Carlos Ruiz Zafón':
        "«الكتب مرايا: لا ترى فيها إلا ما تحمله بالفعل في داخلك.» — كارلوس رويث ثافون",
    '"Every book has a destination, and every reader has a journey." — Book Distribution Philosophy':
        "«كل كتاب له وجهة، وكل قارئ له رحلة.» — فلسفة توزيع الكتب",
    '"We do not simply move books; we move knowledge, ideas, and imagination." — Book Distribution Philosophy':
        "«نحن لا ننقل الكتب فقط؛ بل ننقل المعرفة والأفكار والخيال.» — فلسفة توزيع الكتب",
    '"A warehouse full of books is a warehouse full of possibilities." — Book Distribution Philosophy':
        "«مستودع مليء بالكتب هو مستودع مليء بالإمكانيات.» — فلسفة توزيع الكتب",
    '"Every delivered book is a new story beginning somewhere." — Book Distribution Philosophy':
        "«كل كتاب يتم تسليمه هو قصة جديدة تبدأ في مكان ما.» — فلسفة توزيع الكتب",
    '"Behind every order is a reader waiting for discovery." — Book Distribution Philosophy':
        "«خلف كل طلب يوجد قارئ في انتظار الاكتشاف.» — فلسفة توزيع الكتب",
    '"Distribution turns printed pages into shared experiences." — Book Distribution Philosophy':
        "«التوزيع يحوّل الصفحات المطبوعة إلى تجارب مشتركة.» — فلسفة توزيع الكتب",
    '"Every package carries imagination, knowledge, and opportunity." — Book Distribution Philosophy':
        "«كل طرد يحمل خيالًا ومعرفة وفرصة.» — فلسفة توزيع الكتب",
    '"A distributor is the bridge between authors and readers." — Book Distribution Philosophy':
        "«الموزّع هو الجسر بين المؤلفين والقراء.» — فلسفة توزيع الكتب",
    '"The journey of knowledge begins with accessibility." — Book Distribution Philosophy':
        "«رحلة المعرفة تبدأ بسهولة الوصول إليها.» — فلسفة توزيع الكتب",
    '"Books travel so minds can explore." — Book Distribution Philosophy':
        "«الكتب تسافر لتتمكن العقول من الاستكشاف.» — فلسفة توزيع الكتب",
    '"The goal as a company is to have customer service that is not just the best but legendary." — Sam Walton':
        "«هدف الشركة هو تقديم خدمة عملاء ليست الأفضل فقط، بل أسطورية.» — سام والتون",
    '"Quality is the best business plan." — John Lasseter':
        "«الجودة هي أفضل خطة عمل.» — جون لاسيتر",
    '"Great things in business are never done by one person. They are done by a team of people." — Steve Jobs':
        "«الأشياء العظيمة في العمل لا يحققها شخص واحد، بل يحققها فريق من الأشخاص.» — ستيف جوبز",
    '"Efficiency is doing better what is already being done." — Peter Drucker':
        "«الكفاءة هي أن تؤدي بشكل أفضل ما يُؤدى بالفعل.» — بيتر دراكر",
    '"The best way to predict the future is to create it." — Peter Drucker':
        "«أفضل طريقة للتنبؤ بالمستقبل هي صنعه.» — بيتر دراكر",
}


def escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def write_po(translations, lang, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        f.write(HEADER.format(lang=lang))

        for msgid, msgstr in translations.items():
            f.write(f'msgid "{escape(msgid)}"\n')
            f.write(f'msgstr "{escape(msgstr)}"\n\n')


def main():
    fr_po = os.path.join(BASE_DIR, "locale", "fr", "LC_MESSAGES", "django.po")
    ar_po = os.path.join(BASE_DIR, "locale", "ar", "LC_MESSAGES", "django.po")

    write_po(FR, "fr", fr_po)
    write_po(AR, "ar", ar_po)

    for po in (fr_po, ar_po):
        mo = po[:-3] + ".mo"
        subprocess.check_call([sys.executable, os.path.join(BASE_DIR, "scripts", "msgfmt.py"), po, mo])


if __name__ == "__main__":
    main()
