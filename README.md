# YSSE Social Entrepreneurship Summit 2027

## প্রজেক্টের অবস্থা

এটি Django 5.2 LTS-ভিত্তিক project। এখানে settings, environment configuration, SQLite/PostgreSQL database selection, logging, Django Admin এবং ASGI/WSGI entry point আছে। `home` app-এ database-managed homepage ও একক summit event configuration, `summit` app-এ event details page, `about` app-এ editable YSSE history ও initiatives, `competitions` app-এ ছয়টি competition ও private participant submission workflow, `speakers` app-এ speaker/VVIP directory, `schedule` app-এ editable event agenda, `partners` app-এ sponsor listing ও partnership inquiry workflow, `registrations` app-এ pass management/registration/ticketing, `payments` app-এ provider-neutral payment adapter ও local development demo, `contact` app-এ public contact, FAQs, policies, press releases ও newsletter, এবং `dashboard` app-এ permission-controlled moderator dashboard রয়েছে।

## Architecture

Business domain অনুযায়ী app ভাগ করা হয়েছে; ভবিষ্যৎ ও অনির্বাচিত workflow-এর জন্য আলাদা app আগেভাগে তৈরি করা হয়নি।

| App | দায়িত্ব |
| --- | --- |
| `core` | Shared utilities, common layout/context, health বা সাধারণ site-wide behavior |
| `home` | Homepage rendering, single event configuration, homepage highlights, reusable speaker profiles/featured selection এবং published announcements |
| `about` | Historical timeline, unpublished impact statistics, founder vision এবং YSSE wings |
| `summit` | Shared `home.SummitEvent` data দিয়ে Summit details page rendering |
| `competitions` | ছয়টি competition-এর details, category-specific submissions, private files, reviewer workflows ও CSV export |
| `speakers` | Speaker category taxonomy, published directory/filter/profile pages; profile records reuse `home.FeaturedSpeaker` |
| `schedule` | Summit event configuration-এর সঙ্গে যুক্ত agenda sessions, speakers ও competitions |
| `partners` | Editable sponsor tiers, published sponsor directory, managed pitch deck এবং inquiry review |
| `registrations` | Configurable passes, authenticated participant registrations, tickets, staff payment review ও reports |
| `payments` | Provider-neutral transaction attempts, verified callback processing, idempotent payment events ও read-only diagnostics |
| `contact` | Global footer, official contact/social settings, private contact inquiries, FAQs, organizer-reviewed policies, press releases, double-opt-in newsletter |
| `dashboard` | Staff/group-protected management overview, permission-aware Admin shortcuts, paginated private registration table, aggregate reports ও CSV export |

Dashboard Django Admin-এর পাশাপাশি চলে; কোনো management workflow বা permission bypass করে না। Registration-এ Django-এর built-in User ব্যবহার হয়; custom user model বা public self-signup এই module-এ যোগ করা হয়নি।

## প্রস্তাবিত folder structure

```text
project-root/
├── manage.py
├── requirements.txt
├── .env.example
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
├── apps/
│   ├── core/
│   └── home/
│       ├── models.py
│       ├── views.py
│       ├── services.py
│       ├── admin.py
│       ├── tests.py
│       ├── templates/home/
│       └── static/home/
│   └── about/
│       ├── models.py
│       ├── views.py
│       ├── services.py
│       ├── admin.py
│       ├── tests.py
│       ├── templates/about/
│       └── static/about/
│   └── summit/
│       ├── views.py
│       ├── services.py
│       ├── urls.py
│       ├── tests.py
│       ├── templates/summit/
│       └── static/summit/
│   └── competitions/
│       ├── models.py
│       ├── forms.py
│       ├── validators.py
│       ├── views.py
│       ├── services.py
│       ├── admin.py
│       ├── tests.py
│       ├── migrations/
│       ├── templates/competitions/
│       └── static/competitions/
│   └── speakers/
│       ├── models.py
│       ├── views.py
│       ├── urls.py
│       ├── admin.py
│       ├── tests.py
│       ├── migrations/
│       ├── templates/speakers/
│       └── static/speakers/
│   └── schedule/
│       ├── models.py
│       ├── views.py
│       ├── urls.py
│       ├── admin.py
│       ├── tests.py
│       ├── migrations/
│       ├── templates/schedule/
│       └── static/schedule/
│   └── partners/
│       ├── models.py
│       ├── forms.py
│       ├── validators.py
│       ├── services.py
│       ├── views.py
│       ├── urls.py
│       ├── admin.py
│       ├── tests.py
│       ├── migrations/
│       ├── templates/partners/
│       └── static/partners/
│   └── registrations/
│       ├── models.py
│       ├── forms.py
│       ├── services.py
│       ├── views.py
│       ├── urls.py
│       ├── admin.py
│       ├── tests.py
│       ├── migrations/
│       ├── templates/registrations/
│       └── static/registrations/
│   └── payments/
│       ├── models.py
│       ├── services.py
│       ├── providers/
│       ├── views.py
│       ├── urls.py
│       ├── admin.py
│       ├── tests.py
│       ├── migrations/
│       └── templates/payments/
│   └── contact/
│       ├── models.py
│       ├── forms.py
│       ├── validators.py
│       ├── services.py
│       ├── context_processors.py
│       ├── views.py
│       ├── urls.py
│       ├── admin.py
│       ├── tests.py
│       ├── migrations/
│       ├── templates/contact/
│       └── static/contact/
├── templates/
│   └── partials/
├── static/css/
└── media/
```

প্রতিটি domain app-এ প্রয়োজন অনুযায়ী `models.py`, `forms.py`, `views.py`, `urls.py`, `admin.py`, `services.py`, `tests.py`, app-ভিত্তিক `templates/` এবং `static/` থাকবে। ছোট বা অনুপস্থিত feature-এর জন্য অপ্রয়োজনীয় empty file তৈরি করা হবে না।

## প্রাথমিক database design

`home` app-এর initial migration-এ নিচের model আছে:

- `SummitEvent`: একক configuration row; event name, theme, description, timezone-aware start/end, proposed/final venue, status, optional banner/hero media, registration availability এবং CTA রাখে। Homepage countdown ও Summit details page একই row ব্যবহার করে।
- `HomeHighlight`: প্রকাশযোগ্য ও ordered homepage card।
- `FeaturedSpeaker`: shared speaker profile, unique slug, portrait, designation, organization, biography, session topic, multiple speaker categories, participation confirmation, display order এবং explicit `is_published`/`is_featured` controls। Homepage ও `/speakers/` directory একই records ব্যবহার করে; duplicate profile model নেই।
- `Announcement`: draft/published/archived news item; future-dated বা unpublished item public homepage-এ দেখা যায় না।

Migration একটি editable summit configuration এবং কেবল অনুরোধে নিশ্চিত পাঁচটি highlight seed করে। কোনো কাল্পনিক speaker বা announcement যোগ করে না। Uploadable images-এর সর্বোচ্চ আকার 5 MB; image content Pillow দিয়ে যাচাই হয়।

`about` app-এর initial migration-এ:

- `TimelinePeriod` → একাধিক `TimelineMilestone`: prompt-এ দেওয়া চারটি সময়কাল এবং তাদের নির্দিষ্ট milestones। Timeline content staff Admin-এ সম্পাদনা, reorder ও unpublish করা যায়।
- `ImpactStatistic`: প্রস্তাবিত চারটি সংখ্যা database-এ রাখা আছে, কিন্তু সব `is_published=False`; verified না হওয়া পর্যন্ত public page-এ কোনো সংখ্যা দেখা যায় না।
- `FounderVision`: single editable founder/president profile; name, designation এবং written message না থাকলে publish validation ব্যর্থ হয়। Profile photo, HTTPS video link ও thumbnail ঐচ্ছিক।
- `YSSEWing`: YSSE Academy, YSSE Shop, YSSE IT এবং YSSE Blog-এর editable, reorderable cards। Image এবং external HTTPS link ঐচ্ছিক।

`summit` app-এর `/summit/` page `home.SummitEvent`-এর একই configuration render করে; এটি আলাদা Event model তৈরি করে না। Event start/end timezone-aware এবং Asia/Dhaka timezone-এ প্রদর্শিত হয়। Admin থেকে final venue, status, banner, theme, description এবং registration availability পরিবর্তন করা যায়। `home.0002_summit_details` schema যোগ করে ও খালি venue-তে প্রস্তাবিত venue বসায়; `home.0003_alter_summitevent_venue` venue editor-এর সহায়ক নির্দেশনা আপডেট করে। Registration বন্ধ করলে Summit ও homepage-এর মূল Register Now buttons disabled state দেখায়। Verification command: `python manage.py test apps.summit apps.home`.

Initial migration-এ ব্যবহারকারী-প্রদত্ত historical facts-ই seed করা হয়; অতিরিক্ত timeline fact যোগ করা হয় না। Admin permission Django-এর model-level add/change/delete/view permissions অনুসরণ করে। About regression tests: `python manage.py test apps.about`.

`competitions` app-এ `Competition` ছয়টি category, rules, date window, publication state ও per-contest submission policy রাখে। `CompetitionSubmission` built-in User-এর সঙ্গে one-entry-per-competition participant হিসেবে যুক্ত; JSON data category-specific form থেকে আসে এবং reference UUID unique। DB constraint duplicate participant entry আটকায়। Migration ছয়টি requested competition-কে `Upcoming` হিসেবে publish করে, কিন্তু অনির্ধারিত eligibility/rules/deadline বানায় না; opening time ও deadline Admin-এ নির্ধারণ না করা পর্যন্ত submissions বন্ধ থাকে।

`speakers` app-এর `/speakers/` directory-তে category filters এবং প্রতিটি published profile-এর accessible dedicated page আছে (`/speakers/<slug>/`)। `SpeakerCategory`-তে requested six categories seed করা হয়েছে। এক profile একাধিক category-তে যুক্ত হতে পারে; profile model `home.FeaturedSpeaker`-এই থাকে। Public directory ও homepage-এ দেখাতে profile-কে published হতে হবে এবং অন্তত একটি published category-তে যুক্ত থাকতে হবে; category না দেওয়া পুরোনো/demo profile স্বয়ংক্রিয়ভাবে আড়ালে থাকে, তবে Admin-এ সংরক্ষিত থাকে। Participation `Confirmed` বা `Participation not confirmed` হিসেবে স্পষ্টভাবে দেখানো হয়; profile photo ও session topic ঐচ্ছিক। Profile ও category Django Admin-এর নিজ নিজ model permissions দিয়ে পরিচালিত হয়। Homepage featured cards একই profile page-এ link করে। Deploy/setup-এ `python manage.py migrate` চালান; schema ও category seed migration হলো `speakers.0001_initial`, `home.0005_alter_featuredspeaker_options_and_more`, এবং `home.0006_alter_featuredspeaker_slug`। Existing profiles রাখা হয়, slug backfill হয়, এবং legacy participation-কে unconfirmed হিসেবে সেট করা হয়—কোনো নাম/বায়োগ্রাফি বানানো হয় না। Speaker regressions: `python manage.py test apps.speakers apps.home`.

`schedule` app-এর `/schedule/` page chronological, mobile-friendly agenda দেখায়। `ScheduleSession` একই `home.SummitEvent` record-এর সঙ্গে যুক্ত; start/end date-time Asia/Dhaka-তে দেখানো হয়। Optional speaker relation বিদ্যমান `home.FeaturedSpeaker`-এ এবং competition relation বিদ্যমান `competitions.Competition`-এ যায়। End datetime অনুপস্থিত থাকতে পারে, কিন্তু থাকলে start-এর পরে হতে হবে (model validation ও database check constraint)। `schedule.0001_initial` ব্যবহারকারীর দেওয়া ছয়টি agenda title configured summit date-এ seed করে; সময় ও title Admin থেকে পরিবর্তনযোগ্য, কোনো session-এর end time/venue/speaker/competition বানিয়ে দেওয়া হয়নি। Admin-এর Schedule entries-এ search, filters, chronological date hierarchy, ordering ও publish/unpublish পাওয়া যায়। Migration: `python manage.py migrate`; checks: `python manage.py test apps.schedule`.

`partners` app-এর `/partners/` page-এ প্রকাশিত sponsor ও partner-দের category-tier ভিত্তিক logo grid দেখায়। `SponsorCategory`-তে Title Sponsor, Co-Sponsors, Platinum Sponsors, Gold Sponsors, Strategic Partners এবং Media Partners seed করা হয়; category নাম/ক্রম Admin থেকে পরিবর্তনযোগ্য। Sponsor logo Pillow দিয়ে যাচাই ও 5 MB-এ সীমিত, website URL HTTPS হতে হবে, এবং কেবল published sponsor/public category দেখানো হয়। কোনো sponsor বা partnership approval কল্পনা করে তৈরি করা হয় না। `PartnershipSettings` Admin-এ managed PDF deck রাখে; `.pdf` extension, 20 MB limit, PDF signature ও EOF marker যাচাইয়ের পর authorized Admin editor তা প্রকাশ করতে পারে; public download view attachment ও `nosniff` header দেয়।

Partnership inquiry form কেবল organization, contact name, business email, optional phone, interest, message এবং contact consent নেয়। Server-side form validation, CSRF, hidden honeypot এবং configured Django cache দিয়ে per-IP প্রতি ঘণ্টায় সর্বোচ্চ পাঁচটি accepted inquiry limit করা হয়; multi-worker production-এ সমন্বিত throttling-এর জন্য shared cache backend configure করুন। Inquiry default `New`, Admin review/change অনুমতি ছাড়া private, এবং কোনো submission স্বয়ংক্রিয়ভাবে partnership approval হয় না। Admin status বদলালে review timestamp রাখা হয়। Migration ও category seeds: `partners.0001_initial`; apply: `python manage.py migrate`; tests: `python manage.py test apps.partners`.

`registrations` app-এর `/registrations/` route-এ বর্তমানে বিক্রির জন্য প্রকাশিত pass দেখা যায়। `PassType`-এ YSSE Member Pass, General Delegate Pass এবং VIP Pass seed করা আছে, কিন্তু সবগুলোই unpublished/unavailable এবং price `NULL`; organizer-এর confirmed price ছাড়া কোনো মূল্য বা pass বিক্রি দেখানো হয় না। Admin-এ price, confirmation flag, benefits, capacity, sale window, publication/availability ও optional approval policy configure করতে হবে। Event-level registration availability একই `home.SummitEvent`-এর configuration থেকে আসে।

Summit registration-এর জন্য sign-in/account দরকার নেই। Guest-এর নাম, business email, optional phone এবং pass detail দিয়ে registration করা যায়; guest registration authenticated session-এর সাথে যুক্ত না হয়ে `participant=NULL` হিসেবে রাখা হয় এবং একই event-এ একই email দিয়ে duplicate ঠেকাতে conditional unique constraint থাকে। Pending registration, payment এবং details ওই browser session-এ সীমাবদ্ধ; browser/session হারালে staff-এর সহায়তা লাগবে। Confirmed guest ticket-এর UUID reference email-এ পাঠানো হয় এবং সেটিই ticket-এর private bearer link—এটি অন্যের সাথে শেয়ার করবেন না। Participant-এর একটি Summit registration database constraint-এ সীমাবদ্ধ। Pass name, benefits ও amount registration-সময় snapshot হয়। নতুন registration reference নামের প্রথম চারটি ASCII অক্ষর (যেমন `AMIN`) এবং ছয়টি random অক্ষরের suffix দিয়ে তৈরি হয় (যেমন `AMIN-7K4P2Z`); unique database constraint ও collision retry duplicate আটকায়। Bangla-only/non-ASCII name-এর prefix `REG` হয়। Existing UUID registration reference migration-এ string হিসেবে অপরিবর্তিত থাকে, এবং registration detail URL পুরোনো UUID reference-ও গ্রহণ করে। Paid passes initially `Pending` থাকে। কোনো browser return-কে payment proof ধরা হয় না: configured provider adapter-এর server-side verification সফল হলেই payment status update হয় এবং প্রযোজ্য হলে UUID ticket তৈরি হয়। Adapter না থাকলে checkout options বন্ধ থাকে এবং registration pending-ই থাকে; তখন অনুমোদিত staff trusted merchant records মিলিয়ে audited Admin reconciliation করতে পারেন। Free (`0.00`) confirmed pass-এ payment `Not required`; manual approval flag থাকলে confirmation/ticket আলাদা staff action লাগে। Entry verification-এর জন্য `verify_tickets_at_entry` permission লাগে। Printable ticket-এ contact email/phone বা QR payload নেই; ticket reference নিজেই entry lookup key।

Admin roles-এ sensitive registration data দেখার জন্য `view_registration`/`change_registration` প্রয়োজন। সাধারণ payment review-এর জন্য `verify_registration_payments`; paid বা refunded status-এ manual reconciliation-এর জন্য পৃথক `reconcile_registration_payments` permission এবং audit note দরকার। Registration approve/cancel-এর জন্য `manage_registration_status` দিন। Ticket checker-কে কেবল `verify_tickets_at_entry` এবং Admin access প্রয়োজন। Payment/refund/cancellation actions audit note চায়; cancellation নিজে থেকে refund বোঝায় না। CSV report-এ participant email ও phone বাদ থাকে। Registration notice, payment status, ticket delivery, cancellation এবং refund-এর email service আছে; local default email console-এ লেখা হয়, deployment-এ `.env`/secret store-এ SMTP সেট করুন। কোনো email password source control-এ রাখবেন না।

Registration migration ও seeded draft passes: `registrations.0001_initial`; guest registration ও per-event guest email constraint-এর migration হলো `registrations.0005_guest_registration_access`। Apply করতে `python manage.py migrate`। Tests: `python manage.py test apps.registrations`; পূর্ণ regression command নিচে।

### Moderator management dashboard

Moderator এবং superuser-দের sign-in page হলো `/admin-2/`; সঠিক staff/group permission ছাড়া সেখানে login সম্পন্ন হয় না। Login-এর পর custom interface `/dashboard/`-এ যায়। Django Admin আলাদা interface হিসেবে `/ysse2014/`-এ থাকে। URL-এর নাম access control নয়—দুই interface-এই authentication ও server-side permissions বাধ্যতামূলক। `/dashboard/records/`-এ Admin-registered সব model-এর permission-filtered directory আছে; list, search, filter, sort, pagination, details, add/edit forms, inline forms, confirmed delete, list-editable fields এবং registered bulk actions একই custom dashboard UI-তে পাওয়া যায়। Superuser সব অনুমোদিত Admin-registered model ও তার Admin-configured actions দেখতে/ব্যবহার করতে পারে; অন্যান্য staff-এর access Django-এর module/model/object permissions ও সংশ্লিষ্ট `ModelAdmin` restrictions অনুযায়ী সীমিত। Moderator sidebar-এর management shortcut-গুলোও dashboard list-এ যায়; dashboard-এর মধ্যে Django Admin navigation link নেই। Django Admin আলাদা interface হিসেবে চালু থাকে। Anonymous visitor login-এ redirect হয়, কিন্তু login করা সাধারণ user বা `is_staff` user-ও moderator role ছাড়া 403 পায়। Django Admin index-এ `Admin Panel 2` card শুধু moderator ও superuser-এর জন্য দেখা যায়। URL সবাইকে পাঠানো যাবে, কিন্তু URL নিজে কাউকে access দেয় না।

Deploy/local database-এ role ও baseline module permissions তৈরি বা refresh করুন:

```powershell
python manage.py migrate
python manage.py setup_summit_moderators
```

তারপর Django Admin → Authentication and Authorization → Users-এ নির্দিষ্ট account-কে **Staff status** দিন এবং **YSSE Summit Moderators** group-এ যোগ করুন। Group, user, staff flag ও model permissions production database-এ configure হলে একই `/dashboard/` URL live domain-এ কাজ করবে। Moderator baseline-এ registration read/status actions, competition review, speakers, schedule, sponsors, private contact inquiry এবং read-only payment transaction access আছে; payment reconciliation-to-paid এবং newsletter subscriber email access আলাদা করে অনুমোদিত কর্মীদের দিন। Link লুকানো access control নয়—প্রতিটি dashboard view server-side moderator membership যাচাই করে এবং metrics/shortcuts model permission অনুসারে filter হয়।

Dashboard-এ real database counts, registration pass breakdown, PII-minimized paginated registration table, status/pass/date filters, permission-gated management links, all-records directory, searchable/filterable/sortable lists, fieldset-based details, Admin-configured add/edit forms, inlines, inline list edits, confirmed single/bulk delete, configured bulk actions ও date-filtered aggregate CSV reports আছে। Model listing, forms, deletion ও actions user-এর Django permissions, `ModelAdmin` hooks, queryset scoping ও readonly configuration অনুসরণ করে; subscriber/payment data ও audited payment transitions-এর existing safeguards বজায় থাকে। CSV report-এ participant identity, email, phone বা competition submission content থাকে না। Empty metrics শূন্য হিসেবে দেখায়। App কোনো dashboard-owned data model তৈরি করে না, তাই নতুন migration প্রয়োজন নেই। Tests: `python manage.py test apps.dashboard`.

### Contact, FAQ, policies, press এবং newsletter

`contact` app এই public communication surface-গুলো আলাদা মডেলে পরিচালনা করে:

- `/contact/` official `ysse.hrd@gmail.com`, Admin-configured hotline/address/social URLs এবং private contact inquiry দেখায়। Inquiry form server-side validation, consent, hidden honeypot ও প্রতি IP প্রতি ঘণ্টায় পাঁচটি submission limit প্রয়োগ করে। Inquiry `New` status-এ database-এ থাকে, public করা হয় না, এবং authorized staff Django Admin permissions দিয়ে review করেন।
- `/contact/faq/` শুধু প্রকাশিত FAQ দেখায়; category ও display order Admin থেকে নিয়ন্ত্রণযোগ্য। কোনো event/payment তথ্য নিশ্চিত না হওয়া পর্যন্ত FAQ seed করা হয়নি।
- `/contact/privacy/` ও `/contact/terms/`-এর editable initial draft migration দিয়ে আসে, কিন্তু `organizer_reviewed=False` ও `is_published=False`; review না হওয়া পর্যন্ত public draft content দেখানো হয় না। Admin-এ review চিহ্নিত না করে publish করলে form validation ব্যর্থ হয়। এগুলো legal advice বা compliance certification নয়।
- `/contact/press/` কেবল প্রকাশিত ও publication date পেরোনো release দেখায়। Cover image ও PDF attachment `PRIVATE_MEDIA_ROOT`-এ রাখা হয় এবং authenticated admin upload workflow-এ validation হয়; public attachment/cover route-এ release publish ও due-date check দুটোই আবশ্যক। PDF attachment-এর extension, 10 MB size এবং signature/EOF যাচাই হয়।
- Footer-এ quick links, FAQ/privacy/terms/press, official email/contact fields এবং newsletter signup আছে। Facebook, LinkedIn, YouTube, Instagram URL `Public information settings` Admin থেকে configure করতে হয়; কোনো official profile URL অনুমান করে যোগ করা হয়নি।
- Newsletter email lower-case normalize করে unique রাখা হয়; explicit consent-এর পরে confirmation mail পাঠানো হয় এবং confirmation token ব্যবহার না করা পর্যন্ত status `Pending` থাকে। Subscriber email public page-এ প্রকাশিত হয় না। Confirmation mail-এ private unsubscribe link থাকে; GET request শুধু confirmation form দেখায়, CSRF-protected POST-এ unsubscribe সম্পন্ন হয়। Email পাঠাতে existing environment-driven Django email backend ব্যবহৃত হয়।

Migration `contact.0001_initial` official email settings এবং review/unpublished Privacy Policy ও Terms draft তৈরি করে। Apply ও যাচাই:

```powershell
python manage.py migrate
python manage.py showmigrations contact
python manage.py test apps.contact
```

Production-এ contact/newsletter rate limiting-এর জন্য shared Django cache backend configure করুন; local-memory cache একাধিক worker-এর মধ্যে limit ভাগ করে না। `.env`-এ SMTP/transactional email credentials secret store থেকে configure করুন। Newsletter email delivery না হলে subscription pending থাকে এবং endpoint `503` দেয়; retry-তে confirmation mail আবার পাঠানো হয়।

Contact form submission `contact.ContactInquiry` টেবিলে private record হিসেবে জমা হয়। Django Admin → Contact inquiries-এ status/notes দিয়ে review করুন; inquiry select করে “Download selected inquiries as CSV” action দিয়ে CSV export করা যায়। Export-এ private contact data থাকায় inquiry view permission কেবল অনুমোদিত staff-কে দিন।

### Payment adapter: বর্তমান সক্ষমতা ও prerequisites

`payments` app registration workflow-কে প্রতিস্থাপন করে না। Adapter contract (`apps/payments/providers/base.py`) hosted checkout শুরু করা, callback থেকে internal reference বের করা, signature/callback যাচাই করা এবং provider-এর authoritative server API দিয়ে amount/currency/status নিশ্চিত করার জন্য। Transaction-এ registration, amount/currency snapshot, method, unique internal reference, provider reference, expiry, verified time ও status থাকে। Callback payload সংরক্ষণ হয় না; কেবল digest এবং sanitized diagnostic/event code থাকে। একই registration-এর জন্য এক সময়ে একটি unexpired checkout চালু থাকে। Browser return কেবল database-এর অবস্থা দেখায়, paid করে না। Payment সফল হলে transaction এবং registration/ticket এক atomic workflow-তে reconcile হয়; দেরিতে পাওয়া success ticket ইস্যু না করে staff reconciliation-এর জন্য চিহ্নিত হয়।

এই workspace-এ কোনো live বা merchant sandbox gateway adapter/credential নেই। Local `development` settings-এ `apps.payments.providers.demo.DemoProvider` চারটি planned method-ই simulated checkout হিসেবে দেখায়; participant success/failure/cancel result নির্বাচন করে registration flow পরীক্ষা করতে পারে। এই demo-তে কোনো gateway call বা অর্থ লেনদেন হয় না, transaction স্পষ্টভাবে `demo-sandbox` হিসেবে চিহ্নিত, এবং adapter কেবল `PAYMENT_DEMO_ENABLED=True` হলে কাজ করে। Production settings-এ demo mode সবসময় বন্ধ; merchant adapter নির্দিষ্ট না থাকলে `apps.payments.providers.unconfigured.UnconfiguredProvider` fail-closed default থাকে। `.env.example` কেবল local development-এর জন্য demo provider দেখায়—production environment-এ approved provider বা unconfigured provider ব্যবহার করুন। Gateway SDK dependency যোগ করা হয়নি; real payment ও merchant sandbox verification এখনও credentials, approval এবং provider integration-এর অপেক্ষায়।

Official-source যাচাই অনুযায়ী:

- [bKash Developer documentation](https://developer.bka.sh/llms.txt) tokenized checkout-এর create, execute এবং query API নথিভুক্ত করে; merchant/app credentials এবং sandbox access ছাড়া adapter চালানো যাবে না।
- [SSLCOMMERZ v4 developer documentation](https://developer.sslcommerz.com/doc/v4/) hosted checkout, IPN এবং server-side Order Validation API নথিভুক্ত করে। Gateway-এর advertised card/MFS option প্রতিটি merchant account-এ চালু আছে ধরে নেওয়া যাবে না; onboarding ও sandbox-এ exact channel verify করতে হবে।
- Nagad-এর public official integration documentation এই implementation-এ যাচাই করা যায়নি। Rocket-এর [official merchant payment page](https://www.dutchbanglabank.com/rocket/merchant-payment.html) merchant payment পদ্ধতি বর্ণনা করে, কিন্তু এই গবেষণায় online merchant API specification নিশ্চিত হয়নি। তাই bKash, Nagad, Rocket বা card-এর direct/API support এখনো implemented/verified নয়; SSLCOMMERZ-এর মতো approved gateway-তে channel availability account থেকে নিশ্চিত করতে হবে।

Provider চালু করার আগে organizer/merchant-এর লিখিত approval, provider-issued sandbox credentials, official API/version documentation, HTTPS callback URL/allowlist এবং account-এ কোন payment channels সক্রিয়—এসব নিশ্চিত করুন। `.env`/secret store-এ `PUBLIC_BASE_URL`-এ canonical HTTPS origin সেট করুন; adapter checkout method advertise করবে না যদি এই canonical origin অনুপস্থিত/invalid হয়। Secret deployment environment/secret store-এ রাখুন; URL, callback body বা card data log করবেন না। Approved provider adapter-এ callback signature যাচাই এবং server-to-server transaction lookup বাধ্যতামূলক। `/payments/callback/` কেবল provider notification endpoint; `/payments/return/<reference>/` browser return। `payments.view_paymenttransaction` attempt দেখার জন্য এবং `payments.view_payment_diagnostics` sanitized audit-event দেখার জন্য দিন। Admin-এ payment attempt read-only; registration-এর manual paid/refund reconciliation-এর জন্য আলাদা `reconcile_registration_payments` permission, trusted independent evidence এবং audit note লাগে। ওই audited action চালালে `payment_reviewed_at` ও `payment_reviewed_by` স্বয়ংক্রিয়ভাবে পূরণ হয়; timestamp/reviewer সরাসরি সম্পাদনযোগ্য নয়। Refund provider API call স্বয়ংক্রিয় নয়—Admin workflow কেবল externally completed refund-এর audited record হিসেবে ব্যবহৃত হয়।

Registration reference migration `registrations.0004_alter_registration_reference`; এটি বিদ্যমান reference data রেখে UUID column-কে text reference field-এ পরিবর্তন করে। Payment migrations `payments.0001_initial`, `payments.0002_paymentevent_payment_callback_digest_unique` ও `payments.0003_paymenttransaction_one_pending_payment_per_reg`; registration-এর reconciliation permission `registrations.0003_alter_registration_options`-এ। নতুন checkout structure local database-এ যোগ করতে:

```powershell
python manage.py migrate
python manage.py showmigrations registrations payments
python manage.py test apps.payments apps.registrations
```

Tests fake adapter-এ success/failure, invalid ও duplicate callbacks, amount/currency mismatch, timeout, owner/Admin permission, duplicate checkout, reconciliation ও refund sync পরীক্ষা করে; development demo flow-ও real payment ছাড়াই চালানো যায়। Merchant sandbox verification actual merchant sandbox ও approved adapter না আসা পর্যন্ত সম্ভব নয়।

Submission forms Impact pitch deck, debate team/motions/member details, poster artwork, article text/word limits, video file/HTTPS link, এবং self/peer award nomination অনুযায়ী আলাদা validation চালায়। File extension allowlist ও MB limit competition-ভিত্তিক; PDF/image/video signatures এবং DOCX/PPTX container content server-এ যাচাই হয়। Participant files generated UUID names-এ configurable `PRIVATE_MEDIA_ROOT`-এ থাকে, কোনো public URL নেই, এবং owner বা explicit reviewer permission-সহ authenticated download view দিয়েই পাওয়া যায়। `.env.example`-এর default `private_media/` `.gitignore`-এ আছে। Production-এ ওই private directory web-server static/media path হিসেবে serve করবেন না; access-controlled private object storage-এ স্থানান্তর করা যেতে পারে।

Participant submission URL-গুলোতে Django login আবশ্যক; এই module public self-signup তৈরি করে না। Existing Django account প্রয়োজন। Admin-এ authorized reviewer-এর `competitions.review_competitionsubmission` অথবা view/change model permission থাকতে হবে; reviewer entry data পরিবর্তন না করে status ও reviewer notes সম্পাদনা করতে পারেন। CSV export-এ submission metadata থাকে, participant email বা content থাকে না। Lens of Change leaderboard বন্ধ রাখা হয়েছে—কোনো verified scoring/engagement source এখনো configured নয়। Competition checks: `python manage.py test apps.competitions`.

Competition routes: `/competitions/`, `/competitions/<slug>/`, `/competitions/<slug>/submit/`, এবং signed-in participant-এর `/competitions/my-submissions/`। Admin-এর **Competitions** থেকে six records-এর status, publication, dates, rules, allowed extensions ও size limits configure করুন। Rule/eligibility, opening time, deadline, team size, accepted formats এবং upload-size policy যাচাই করার পরেই status `Open for submissions` করুন। Reviewers-কে Django Admin-এ `competitions | competition submission | Can review competition submissions` permission দিন। Local migrations: `competitions.0001_initial` এবং `competitions.0002_competition_article_topic_guidelines_and_more`; homepage Explore CTA-ও `/competitions/`-এ যায়। Tests: `python manage.py test apps.competitions apps.home`.

Registration/payment/newsletter-এর মতো ভবিষ্যৎ module-এর জন্য প্রস্তাবিত সম্পর্ক:

- `Summit`: ভবিষ্যতে summit-wide schedule বা operational metadata প্রয়োজন হলে আলাদা event configuration model যোগ করা যেতে পারে; বর্তমানে shared event configuration `home.SummitEvent`-এ রয়েছে।
- `ScheduleSession`: এক session-এ একাধিক বিদ্যমান speaker যুক্ত করতে হলে পরে explicit junction relationship (`SessionSpeaker`) যোগ করা হবে।
- Provider-neutral payment attempt, callback verification, safe audit event এবং Admin reconciliation layer এখন আছে; তবে approved provider adapter/credentials না থাকায় কোনো live gateway চালু নয়। Payment status কেবল provider server-side verification অথবা আলাদা permission-সহ audited staff reconciliation-এ বদলায়।
- `NewsletterSubscription`-এ normalized email, consent timestamp এবং unsubscribe state থাকবে। Contact inquiry/FAQ-এর জন্য আলাদা model প্রয়োজন অনুযায়ী যোগ হবে।

Money, ownership, uniqueness, status transition, indexes এবং delete behavior সংশ্লিষ্ট module তৈরির সময় migration ও tests-এ নির্দিষ্ট করা হবে। Database-এ existing user data মুছে ফেলা বা reset করার কোনো setup step নেই।

## Dependency নির্বাচন

- `Django>=5.2.8,<5.3`: Django 5.2 LTS; 5.2.8 থেকে Python 3.14 compatibility আছে। Upper bound major/minor breaking upgrade এড়ায়, আর pip একই series-এর compatible patch updates নিতে পারবে।
- `django-environ>=0.12,<0.13`: `.env` এবং `DATABASE_URL` configuration parse করে।
- `psycopg[binary]>=3.2.10,<4`: PostgreSQL driver; local development-এ SQLite চালাতে এটি ব্যবহার হয় না, কিন্তু PostgreSQL-এ যাওয়ার সময় নতুন driver dependency খুঁজতে হবে না। `[binary]` development/standard deployment-এ সহজ installation দেয়; কঠোর production image-এ প্রয়োজন অনুযায়ী source build ব্যবহার করা যেতে পারে।
- `Pillow>=12.0,<13`: Django `ImageField` uploads যাচাই ও image metadata পড়ার জন্য; hero poster, speaker profile এবং announcement image-এ ব্যবহৃত।

Bootstrap/CDN, payment SDK এবং আলাদা test framework এখন যোগ করা হয়নি—সংশ্লিষ্ট feature ও provider নির্বাচন হলে dependency যোগ হবে।

## Local setup (Windows PowerShell)

Python 3.10+ ব্যবহার করুন; এই workspace-এ Python 3.14.8 পাওয়া গেছে।

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`-এ production-এ অবশ্যই `SECRET_KEY` বদলান। Local development-এ `.env`-এর `SECRET_KEY` placeholder-ও কাজ করবে, তবে deploy করবেন না। `.env` Git-এ commit হয় না।

## Database ও development server

```powershell
python manage.py check
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Homepage `http://127.0.0.1:8000/`-এ, moderator/superuser sign-in `http://127.0.0.1:8000/admin-2/`-এ, custom dashboard `http://127.0.0.1:8000/dashboard/`-এ এবং Django Admin `http://127.0.0.1:8000/ysse2014/`-এ চলবে। প্রথমবার চালানোর সময় homepage database migration-ও apply করুন। নতুন model যোগ হলে তার app-এর migration তৈরি ও যাচাই করতে হবে:

```powershell
python manage.py makemigrations
python manage.py migrate
```

About YSSE page: `http://127.0.0.1:8000/about/`। Summit details page: `http://127.0.0.1:8000/summit/`। Timeline, Impact statistics, Founder vision এবং YSSE wings Django Admin থেকে manage করুন। Impact figures যাচাই সম্পন্ন না হওয়া পর্যন্ত `is_published` বন্ধ রাখুন।

Summit title, theme, description, date/time, venue, status, banner এবং registration availability Admin-এর **Summit event configuration** record-এ manage করুন; একই record homepage ও details page উভয়টি ব্যবহার করে। Homepage content-এর জন্য Homepage highlights, Speakers এবং Announcements-ও এখানে manage হয়। Speakers default-এ unpublished থাকে; public করতে `is_published` এবং homepage-এ দেখাতে `is_featured` সক্রিয় করুন। Announcements publish/unpublish/archive করতে Admin list action আছে। Media upload development-এ Django debug media route দিয়ে serve হয়; production-এ media storage আলাদাভাবে configure করতে হবে।

Homepage regression tests চালাতে `python manage.py test apps.home` ব্যবহার করুন।

Registration/Admin ও বিদ্যমান module-গুলোর regression suite চালাতে:

```powershell
python manage.py test apps.contact apps.payments apps.registrations apps.partners apps.schedule apps.speakers apps.competitions apps.summit apps.about apps.home
```

Shared navbar, footer এবং favicon-এর YSSE logo `static/images/ysse-logo.png` থেকে আসে। Footer সব shared page এবং branded 404/500 page-এ থাকে; developer credit খুললে Facebook ও WhatsApp contact link দেখা যায়।

## PostgreSQL-এ স্থানান্তর

Shared settings `DATABASE_URL` থাকলে installed `django-environ` দিয়ে database URL parse করে; আলাদা `dj-database-url` dependency দরকার হয় না। `DB_CONN_MAX_AGE` connection lifetime নিয়ন্ত্রণ করে (default 600 seconds), এবং reused connection ব্যবহারের আগে health check হয়। Serverless database provider-এর connection/pool limit যাচাই করে প্রয়োজনে `DB_CONN_MAX_AGE` কমান। Vercel-এর ephemeral filesystem-এ SQLite production data persist করে না—production-এ managed PostgreSQL ব্যবহার করুন।

PostgreSQL database ও credentials তৈরি করে `.env`-এ `DATABASE_URL=postgres://USER:PASSWORD@HOST:5432/DATABASE` সেট করুন। `psycopg` driver requirements-এ আছে। Deployment-এর আগে backup/restore পরিকল্পনা এবং data migration আলাদাভাবে যাচাই করতে হবে; শুধু backend URL বদলানো SQLite data স্বয়ংক্রিয়ভাবে স্থানান্তর করে না।

Production চালাতে `DJANGO_SETTINGS_MODULE=config.settings.production` সেট করুন, কমপক্ষে ৫০ অক্ষরের `SECRET_KEY`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` দিন এবং HTTPS নিশ্চিত করুন। `.python-version` Vercel-এর supported Python 3.12 runtime নির্ধারণ করে; এটি Django 5.2 dependency-এর Python floor পূরণ করে। Vercel project-এ `DJANGO_SETTINGS_MODULE=config.settings.production` সহ প্রয়োজনীয় environment variables configure করুন। `STATIC_ROOT` configure থাকায় Vercel build-এর সময় স্বয়ংক্রিয়ভাবে `collectstatic` চালিয়ে `/static/` asset CDN-এ পরিবেশন করে; তাই custom build command বা `/static/` route rewrite রাখা হয়নি। WhiteNoise middleware `SecurityMiddleware`-এর ঠিক পরে আছে এবং local `vercel dev`/standalone production-এ hashed manifest static serving দেয়। Vercel `VERCEL_URL` ও `VERCEL_PROJECT_PRODUCTION_URL` যে exact hostnames দেয়, settings সেগুলো `ALLOWED_HOSTS`-এ যোগ করে; custom domain হলে `ALLOWED_HOSTS` environment variable-এ explicit hostname যোগ করুন। `ALLOWED_HOSTS = ['*']` ব্যবহার করবেন না—এটি host validation বন্ধ করে। Production settings অনুপস্থিত/ছোট secret বা hosts-এ startup বন্ধ করে; `DEBUG` সবসময় বন্ধ থাকে। Generic `404.html`/`500.html` visitor-কে diagnostic details দেখায় না। Static/media serving deployment web server/object storage-এ configure করতে হবে।
