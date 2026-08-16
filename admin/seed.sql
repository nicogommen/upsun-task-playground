-- DEMO FIXTURE DATA. NOT APPLIED AUTOMATICALLY.
--
-- Synthetic chat history so the admin looks like it has been in use, rather
-- than showing an empty history nav in a walkthrough. Nothing in the app runs
-- this: storage.connect() applies schema.sql only. Applying it is an explicit,
-- manual step.
--
--   Apply:  upsun sql -p <project> -e <env> -A admin < admin/seed.sql
--   Undo:   upsun sql -p <project> -e <env> -A admin \
--             "DELETE FROM sessions WHERE id LIKE 'seed-%';"
--           (runs cascade via the foreign key)
--
-- Every seeded id is prefixed `seed-`, which is what makes the undo above
-- surgical: real sessions are UUIDs and can never collide with the prefix.
--
-- Timestamps are relative to now(), so the history stays plausible whenever
-- this is applied instead of decaying into obviously stale dates.
--
-- PR links point at real closed PRs in nicogommen/upsun-task-playground, so a
-- click during a demo opens a genuine agent PR rather than a 404.

BEGIN;

-- Re-applying is safe: clear any previous seed first.
DELETE FROM sessions WHERE id LIKE 'seed-%';

INSERT INTO sessions (id, created_at, title) VALUES
  ('seed-s1', now() - interval '22 days', 'Make the hero subtitle clearer and shorter'),
  ('seed-s2', now() - interval '16 days', 'Add a cool gradient background for our hero.'),
  ('seed-s3', now() - interval '9 days',  'Add a new section on our homepage with an image'),
  ('seed-s4', now() - interval '4 days',  'Change the homepage headline to Hello from an agent'),
  ('seed-s5', now() - interval '1 day',   'Add a section below the hero explaining task containers');

INSERT INTO runs
  (id, session_id, prompt, status, target_environment, created_at, completed_at,
   activity_id, branch_name, pr_url, error)
VALUES
  -- Session 1: an early copy tidy-up, three weeks ago.
  ('seed-r01', 'seed-s1', 'Make the hero subtitle clearer and shorter',
   'succeeded', 'main', now() - interval '22 days', now() - interval '22 days' + interval '3 min',
   'seedact01', 'coding-6a1f2c-make-the-hero-subtitle-cl',
   'https://github.com/nicogommen/upsun-task-playground/pull/6', NULL),
  ('seed-r02', 'seed-s1', 'Also shorten the three feature card descriptions to one line each',
   'succeeded', 'main', now() - interval '22 days' + interval '11 min', now() - interval '22 days' + interval '14 min',
   'seedact02', 'coding-9c4d1a-also-shorten-the-three-fe',
   'https://github.com/nicogommen/upsun-task-playground/pull/7', NULL),

  -- Session 2: styling pass.
  ('seed-r03', 'seed-s2', 'Add a cool gradient background for our hero.',
   'succeeded', 'main', now() - interval '16 days', now() - interval '16 days' + interval '4 min',
   'seedact03', 'coding-2f8b3e-add-a-cool-gradient-backgro',
   'https://github.com/nicogommen/upsun-task-playground/pull/7', NULL),
  ('seed-r04', 'seed-s2', 'Make the gradient softer, it is too saturated',
   'succeeded', 'main', now() - interval '16 days' + interval '19 min', now() - interval '16 days' + interval '23 min',
   'seedact04', 'coding-77ac10-make-the-gradient-softer',
   'https://github.com/nicogommen/upsun-task-playground/pull/8', NULL),
  ('seed-r05', 'seed-s2', 'Use the Upsun brand violet for the CTA button',
   'failed', 'main', now() - interval '16 days' + interval '41 min', now() - interval '16 days' + interval '42 min',
   'seedact05', NULL, NULL, 'task failed'),

  -- Session 3: content work, with one abandoned mid-run.
  ('seed-r06', 'seed-s3', 'Add a new section on our homepage with an image and short copy',
   'succeeded', 'main', now() - interval '9 days', now() - interval '9 days' + interval '6 min',
   'seedact06', 'coding-4b9e21-add-a-new-section-on-our-ho',
   'https://github.com/nicogommen/upsun-task-playground/pull/8', NULL),
  ('seed-r07', 'seed-s3', 'Move the new section above the features list',
   'succeeded', 'main', now() - interval '9 days' + interval '15 min', now() - interval '9 days' + interval '18 min',
   'seedact07', 'coding-c03f5d-move-the-new-section-above',
   'https://github.com/nicogommen/upsun-task-playground/pull/9', NULL),
  ('seed-r08', 'seed-s3', 'Add alt text to every image on the homepage',
   'succeeded', 'main', now() - interval '9 days' + interval '38 min', now() - interval '9 days' + interval '41 min',
   'seedact08', 'coding-e51a8f-add-alt-text-to-every-image',
   'https://github.com/nicogommen/upsun-task-playground/pull/10', NULL),

  -- Session 4: the classic demo prompt.
  ('seed-r09', 'seed-s4', 'Change the homepage headline to Hello from an agent',
   'succeeded', 'main', now() - interval '4 days', now() - interval '4 days' + interval '3 min',
   'seedact09', 'coding-1a2b3c-change-the-homepage-headli',
   'https://github.com/nicogommen/upsun-task-playground/pull/9', NULL),
  ('seed-r10', 'seed-s4', 'Add an emoji at the end of the main title',
   'succeeded', 'main', now() - interval '4 days' + interval '9 min', now() - interval '4 days' + interval '12 min',
   'seedact10', 'coding-50c8c8-add-an-emoji-at-the-end-o',
   'https://github.com/nicogommen/upsun-task-playground/pull/11', NULL),
  ('seed-r11', 'seed-s4', 'Revert the emoji, keep the headline change',
   'succeeded', 'main', now() - interval '4 days' + interval '26 min', now() - interval '4 days' + interval '29 min',
   'seedact11', 'coding-88d2f4-revert-the-emoji',
   'https://github.com/nicogommen/upsun-task-playground/pull/10', NULL),

  -- Session 5: yesterday, most recent.
  ('seed-r12', 'seed-s5', 'Add a section below the hero explaining how awesome task containers are',
   'succeeded', 'main', now() - interval '1 day', now() - interval '1 day' + interval '5 min',
   'seedact12', 'coding-882899-add-a-section-below-the-h',
   'https://github.com/nicogommen/upsun-task-playground/pull/12', NULL),
  ('seed-r13', 'seed-s5', 'Tighten the copy in that new section, two sentences max',
   'succeeded', 'main', now() - interval '1 day' + interval '13 min', now() - interval '1 day' + interval '16 min',
   'seedact13', 'coding-b41c77-tighten-the-copy',
   'https://github.com/nicogommen/upsun-task-playground/pull/13', NULL),
  ('seed-r14', 'seed-s5', 'Change the title and add "!!!" at the end',
   'succeeded', 'main', now() - interval '1 day' + interval '34 min', now() - interval '1 day' + interval '37 min',
   'seedact14', 'coding-d1aa9e-change-the-title-and-add',
   'https://github.com/nicogommen/upsun-task-playground/pull/13', NULL);

COMMIT;
